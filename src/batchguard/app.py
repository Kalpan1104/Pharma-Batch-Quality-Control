"""
BatchGuard API (Flask), served by gunicorn on Cloud Run.

    GET  /health         liveness + which model this revision serves
    GET  /v1/model       model identity and its acceptance results
    POST /v1/predict     {"batches": [...]} -> risk, decision, top factors

Run locally:  gunicorn --bind :8080 "batchguard.app:create_app()"
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import joblib
from flask import Flask, jsonify, request

from batchguard import config as C
from batchguard.data import validate_batch
from batchguard.model import MONITORED, explain, predict_risk

MAX_BATCHES = 200


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, **fields}, default=str), flush=True)


def create_app(model_path=None, sink=None):
    from batchguard.sinks import open_sink

    model_path = model_path or C.MODEL_PATH
    bundle = joblib.load(model_path)
    metrics_file = Path(model_path).with_name("metrics.json")
    acceptance = json.loads(metrics_file.read_text()) if metrics_file.exists() else {}
    sink = sink or open_sink(C.PREDICTION_SINK)
    identity = bundle["identity"]
    revision = os.getenv("K_REVISION", "local")

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    log("INFO", "model loaded", model_version=identity["model_version"], revision=revision)

    @app.get("/health")
    def healthz():
        return jsonify(status="ok", model_version=identity["model_version"], revision=revision,
                       threshold=round(bundle["threshold"], 4))

    @app.get("/v1/model")
    def model_info():
        return jsonify(identity=identity, revision=revision, releasable=acceptance.get("releasable"),
                       acceptance=acceptance.get("acceptance", []))

    @app.post("/v1/predict")
    def predict():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("batches"), list):
            return jsonify(error='expected JSON like {"batches": [{...}]}'), 400
        batches = body["batches"]
        if not 1 <= len(batches) <= MAX_BATCHES:
            return jsonify(error=f"send between 1 and {MAX_BATCHES} batches"), 400
        problems = {str(b.get("batch_id", i)) if isinstance(b, dict) else str(i):
                    (validate_batch(b) if isinstance(b, dict) else ["not an object"]) for i, b in enumerate(batches)}
        problems = {k: v for k, v in problems.items() if v}
        if problems:
            return jsonify(error="invalid batches", problems=problems), 422

        risk, X = predict_risk(bundle, batches)
        factors = explain(bundle, X)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        results, rows = [], []
        for i, b in enumerate(batches):
            hold = bool(risk[i] >= bundle["threshold"])
            rid = str(uuid.uuid4())
            results.append({"batch_id": b["batch_id"], "request_id": rid, "risk": round(float(risk[i]), 4),
                            "decision": "hold_for_review" if hold else "proceed",
                            "top_factors": factors[i] if hold else []})
            feats = {c: round(float(X.iloc[i][c]), 6) for c in MONITORED}
            rows.append({"request_id": rid, "ts": now, "revision": revision, "model_version": identity["model_version"],
                         "batch_id": b["batch_id"], "line": b["line"], "supplier": b["supplier"],
                         "risk": round(float(risk[i]), 6), "decision": results[-1]["decision"],
                         "features": feats, "top_factors": factors[i]})
        try:
            sink.write(rows)
        except Exception as e:  # noqa: BLE001 - a logging outage must not stop QA getting an answer
            log("ERROR", "prediction logging failed", error=str(e), n=len(rows))
        return jsonify(model_version=identity["model_version"], revision=revision,
                       threshold=round(bundle["threshold"], 4), results=results)

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="request too large"), 413

    return app
