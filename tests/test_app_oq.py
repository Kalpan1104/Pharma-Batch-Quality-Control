import json

import joblib
import pytest

from batchguard import oq
from batchguard.app import create_app
from batchguard.sinks import JsonlSink


@pytest.fixture
def client(tmp_path, bundle):
    path = tmp_path / "model.joblib"
    joblib.dump(bundle, path)
    (tmp_path / "metrics.json").write_text(json.dumps({"releasable": True, "acceptance": []}))
    app = create_app(str(path), JsonlSink(tmp_path / "p.jsonl"))
    app.config["LOG_FILE"] = tmp_path / "p.jsonl"
    return app.test_client()


def call_with(client):
    def call(method, path, body):
        r = client.open(path, method=method, json=body)
        return r.status_code, r.get_json()
    return call


def test_health(client):
    assert client.get("/health").get_json()["model_version"] == "test-v1"


def test_predict_and_log(client, unlabeled, tmp_path):
    r = client.post("/v1/predict", json={"batches": unlabeled})
    assert r.status_code == 200
    res = r.get_json()["results"]
    assert len(res) == 5 and all(x["decision"] in {"proceed", "hold_for_review"} for x in res)
    assert all(x["top_factors"] for x in res if x["decision"] == "hold_for_review")
    logged = [json.loads(line) for line in (tmp_path / "p.jsonl").read_text().splitlines()]
    assert len(logged) == 5 and "particle_d50" in logged[0]["features"]


@pytest.mark.parametrize("body, status", [(None, 400), ({"batches": []}, 400), ({"batches": "x"}, 400),
                                          ({"batches": [{"batch_id": "x"}]}, 422), ({"batches": [1]}, 422)])
def test_bad_requests(client, body, status):
    assert client.post("/v1/predict", json=body).status_code == status


def test_logging_outage_does_not_block(tmp_path, bundle, unlabeled):
    class Broken:
        def write(self, rows):
            raise RuntimeError("bigquery down")
    path = tmp_path / "m.joblib"
    joblib.dump(bundle, path)
    assert create_app(str(path), Broken()).test_client().post("/v1/predict", json={"batches": unlabeled}).status_code == 200


def test_operational_qualification_passes(client):
    checks = oq.run(call_with(client), expect_version="test-v1", latency_requests=5)
    assert all(c["passed"] for c in checks), [c for c in checks if not c["passed"]]


def test_oq_catches_wrong_version(client):
    checks = {c["id"]: c["passed"] for c in oq.run(call_with(client), expect_version="other", latency_requests=1)}
    assert checks["OQ-2"] is False
