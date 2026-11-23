"""
Daily monitoring job (Cloud Run Job). Compares the last 24 h of predictions with the
training-time profile baked into the same container image as the model.

It also suggests a likely cause, because the fix differs:
  * raw-material shift (new supplier, coarser or wetter material) -> investigate, then retrain
  * process-sensor shift with stable materials and known suppliers  -> check sensor calibration first

Usage: python -m batchguard.monitor [--hours 24]
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from batchguard import config as C
from batchguard.model import share_in_bins
from batchguard.sinks import open_sink
from batchguard.storage import write_text

MATERIAL_FEATURES = {"moisture_in", "particle_d50", "api_potency"}
EPS = 1e-4


def psi(expected, actual):
    e, a = np.clip(np.asarray(expected, float), EPS, None), np.clip(np.asarray(actual, float), EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def level(v):
    return "alert" if v >= C.PSI_ALERT else "warn" if v >= C.PSI_WARN else "ok"


def analyse(profile, rows):
    if len(rows) < C.MIN_MONITOR_ROWS:
        return {"status": "insufficient_data", "n": len(rows), "alerts": [], "checks": {}}
    checks = {}
    for col, ref in profile["features"].items():
        vals = [r["features"][col] for r in rows if col in r["features"]]
        v = psi(ref["share"], share_in_bins(vals, ref["edges"]))
        checks[f"psi_{col}"] = {"value": round(v, 4), "status": level(v)}
    v = psi(profile["risk"]["share"], share_in_bins([r["risk"] for r in rows], profile["risk"]["edges"]))
    checks["psi_risk"] = {"value": round(v, 4), "status": level(v)}
    hold = float(np.mean([r["decision"] == "hold_for_review" for r in rows]))
    shift = abs(hold - profile["hold_rate"])
    checks["hold_rate_shift"] = {"value": round(shift, 4), "live": round(hold, 4), "training": round(profile["hold_rate"], 4),
                                 "status": "alert" if shift >= C.HOLD_RATE_SHIFT_ALERT else "ok"}
    unknown = float(np.mean([r["supplier"] not in profile["suppliers"] for r in rows]))
    checks["unknown_supplier_share"] = {"value": round(unknown, 4),
                                        "status": "alert" if unknown >= C.UNKNOWN_SUPPLIER_ALERT else "ok"}
    alerts = [k for k, c in checks.items() if c["status"] == "alert"]

    material_moved = any(checks[f"psi_{f}"]["status"] == "alert" for f in MATERIAL_FEATURES if f"psi_{f}" in checks)
    process_moved = any(c["status"] == "alert" for k, c in checks.items()
                        if k.startswith("psi_") and k[4:] not in MATERIAL_FEATURES and k != "psi_risk")
    if checks["unknown_supplier_share"]["status"] == "alert" or material_moved:
        cause = "Raw material has changed (new supplier or different material). Investigate, then retrain with recent batches."
    elif process_moved:
        cause = ("Process sensors moved while materials and suppliers look normal. Check sensor calibration "
                 "before retraining; retraining on a miscalibrated sensor would hide the problem.")
    else:
        cause = None

    by_rev = {}
    for r in rows:
        by_rev.setdefault(r["revision"], []).append(r["decision"] == "hold_for_review")
    return {"status": "alert" if alerts else "ok", "n": len(rows), "alerts": alerts, "likely_cause": cause,
            "hold_rate_by_revision": {k: round(float(np.mean(v)), 4) for k, v in by_rev.items()},
            "checks": checks}


def to_markdown(res, hours):
    if res["status"] == "insufficient_data":
        return f"# BatchGuard monitoring\n\nOnly {res['n']} predictions in the last {hours} h (need {C.MIN_MONITOR_ROWS}).\n"
    icon = {"ok": "🟢", "warn": "🟡", "alert": "🔴"}
    out = ["# BatchGuard monitoring", "", f"{res['n']} batches scored in the last {hours} h", "",
           "| Check | Value | Status |", "|---|---|---|"]
    for k, c in res["checks"].items():
        extra = f" (live {c['live']} vs training {c['training']})" if "live" in c else ""
        out.append(f"| {k} | {c['value']}{extra} | {icon[c['status']]} {c['status']} |")
    out += ["", "Hold rate by revision: " + ", ".join(f"`{k}` {v:.1%}" for k, v in res["hold_rate_by_revision"].items()), ""]
    out.append(f"**🔴 {', '.join(res['alerts'])}.** {res['likely_cause'] or ''}" if res["alerts"] else "**🟢 No action needed.**")
    return "\n".join(out) + "\n"


def run(hours=None, profile_path=None, sink=None, reports_uri=None):
    hours = hours or C.MONITOR_WINDOW_HOURS
    profile = json.loads(Path(profile_path or Path(C.MODEL_PATH).with_name("reference_profile.json")).read_text())
    sink = sink or open_sink(C.PREDICTION_SINK)
    res = analyse(profile, sink.read_since(hours))
    res.update(window_hours=hours, model_version=C.MODEL_VERSION,
               generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    md = to_markdown(res, hours)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M")
    base = reports_uri or C.REPORTS_URI
    where = write_text(base, f"monitoring/{stamp}.json", json.dumps(res, indent=2))
    write_text(base, "monitoring/latest.md", md)
    print(json.dumps({"severity": "WARNING" if res["alerts"] else "INFO", "message": "batchguard monitoring",
                      "status": res["status"], "alerts": res["alerts"], "likely_cause": res.get("likely_cause"),
                      "n": res["n"], "report": where}), flush=True)
    print(md)
    return res


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int)
    run(p.parse_args().hours)
