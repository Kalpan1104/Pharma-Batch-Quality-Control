import io
import json
import sys

from batchguard import audit, monitor, traffic
from batchguard.app import create_app
from batchguard.data import generate
from batchguard.model import reference_profile
from batchguard.sinks import BigQuerySink, JsonlSink, open_sink


def score(tmp_path, bundle, scenario, n=200):
    import joblib
    path = tmp_path / "m.joblib"
    joblib.dump(bundle, path)
    sink = JsonlSink(tmp_path / f"{scenario}.jsonl")
    client = create_app(str(path), sink).test_client()
    rows = generate(n, seed=21, scenario=scenario)
    for r in rows:
        r.pop("failed_qc")
    client.post("/v1/predict", json={"batches": rows})
    return sink


def test_monitor_distinguishes_causes(tmp_path, bundle, batches):
    prof_path = tmp_path / "profile.json"
    prof_path.write_text(json.dumps(reference_profile(bundle, batches)))
    results = {s: monitor.run(24, prof_path, score(tmp_path, bundle, s), str(tmp_path / "reports"))
               for s in ("normal", "new_supplier", "sensor_drift")}
    assert results["normal"]["status"] == "ok"
    assert "unknown_supplier_share" in results["new_supplier"]["alerts"]
    assert "Raw material" in results["new_supplier"]["likely_cause"]
    assert "calibration" in results["sensor_drift"]["likely_cause"]
    assert (tmp_path / "reports" / "monitoring" / "latest.md").exists()


def test_monitor_needs_enough_rows():
    assert monitor.analyse({"features": {}}, [])["status"] == "insufficient_data"


def test_audit_record_and_last(tmp_path):
    sink = JsonlSink(tmp_path / "audit.jsonl")
    audit.record("promoted", sink=sink, revision="r1", details={"previous_revision": "r0"})
    audit.record("promoted", sink=sink, revision="r2", details={"previous_revision": "r1"})
    assert audit.last("promoted", sink=sink)["revision"] == "r2"
    assert audit.last("rolled_back", sink=sink) is None


def test_audit_cli(tmp_path, monkeypatch, capsys):
    from batchguard import config as C
    monkeypatch.setattr(C, "AUDIT_SINK", f"jsonl:{tmp_path / 'a.jsonl'}")
    audit.main(["record", "--event", "promoted", "--revision", "r9", "--detail", "previous_revision=r8"])
    capsys.readouterr()
    audit.main(["last", "--event", "promoted", "--field", "details.previous_revision"])
    assert capsys.readouterr().out.strip() == "r8"


class FakeBQ:
    def __init__(self):
        self.rows = []

    def insert_rows_json(self, table, rows):
        self.table = table
        self.rows += rows
        return []


def test_bigquery_sink_serialises_json_fields():
    client = FakeBQ()
    sink = open_sink("bigquery:p.d.t", client=client)
    assert isinstance(sink, BigQuerySink)
    sink.write([{"ts": "2026-01-01T00:00:00+00:00", "event": "x", "details": {"a": 1}}])
    assert client.table == "p.d.t" and client.rows[0]["details"] == '{"a": 1}'


SERVICE = {"status": {"latestReadyRevisionName": "bg-00003",
                      "traffic": [{"revisionName": "bg-00002", "percent": 90},
                                  {"revisionName": "bg-00003", "percent": 10, "tag": "candidate",
                                   "url": "https://candidate---bg.run.app"}]}}


def run_traffic(args, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SERVICE)))
    traffic.main(args)
    return capsys.readouterr().out.strip()


def test_traffic_helpers(monkeypatch, capsys):
    assert run_traffic(["tagged", "candidate"], monkeypatch, capsys) == "bg-00003"
    assert run_traffic(["serving"], monkeypatch, capsys) == "bg-00002"
    assert run_traffic(["url", "candidate"], monkeypatch, capsys) == "https://candidate---bg.run.app"
