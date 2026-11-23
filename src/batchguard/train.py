"""
Train, validate and package a model.

    python -m batchguard.train                      # writes artifacts/, exit 1 if not releasable
    python -m batchguard.train --data-dir data --out artifacts --allow-fail

Outputs in artifacts/:
    model.joblib              bundle: feature builder + model + threshold + identity
    metrics.json              holdout metrics and acceptance results
    reference_profile.json    training-time distributions for monitoring
    validation_report.md      the human-readable release document
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.model_selection import train_test_split

from batchguard import config as C
from batchguard.data import fingerprint, read_jsonl, validate_batch, write_splits
from batchguard.model import evaluate, fit, predict_risk, reference_profile
from batchguard.validation import acceptance, report


def git_sha():
    for var in ("GIT_SHA", "GITHUB_SHA"):          # GIT_SHA is passed into the Docker build
        if os.getenv(var) and os.getenv(var) != "unknown":
            return os.environ[var][:12]
    try:
        return subprocess.check_output(["git", "rev-parse", "--short=12", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def load(data_dir):
    data_dir = Path(data_dir)
    if not (data_dir / "train.jsonl").exists():
        write_splits(data_dir)
    train, holdout = read_jsonl(data_dir / "train.jsonl"), read_jsonl(data_dir / "holdout.jsonl")
    for name, rows in (("train", train), ("holdout", holdout)):
        bad = [(b.get("batch_id"), p) for b in rows if (p := validate_batch(b, require_label=True))]
        if bad:
            raise SystemExit(f"{name}: {len(bad)} batches break the data contract, e.g. {bad[:3]}")
    return train, holdout


def main(data_dir="data", out="artifacts", allow_fail=False, recent_path=None):
    train, holdout = load(data_dir)
    recent_eval, n_recent, recent_fp = None, 0, None
    if recent_path:
        recent = read_jsonl(recent_path)
        bad = [b.get("batch_id") for b in recent if validate_batch(b, require_label=True)]
        if bad:
            raise SystemExit(f"recent data: {len(bad)} batches break the data contract, e.g. {bad[:3]}")
        recent_fit, recent_eval = train_test_split(recent, test_size=0.3, random_state=0,
                                                   stratify=[b[C.TARGET] for b in recent])
        train = train + recent_fit
        n_recent, recent_fp = len(recent), fingerprint(recent)
    labels = [b[C.TARGET] for b in train]
    fit_part, val_part = train_test_split(train, test_size=0.25, random_state=0, stratify=labels)

    bundle = fit(fit_part, val_part)
    metrics = evaluate(bundle, holdout)
    # AC-6: training must be reproducible
    again = fit(fit_part, val_part)
    reproducible = bool(np.array_equal(predict_risk(bundle, holdout)[0], predict_risk(again, holdout)[0]))
    recent_metrics = evaluate(bundle, recent_eval) if recent_eval else None
    criteria = acceptance(metrics, reproducible, recent_metrics)
    releasable = all(c["passed"] for c in criteria)

    identity = {"model_version": os.getenv("MODEL_VERSION", git_sha()), "git_sha": git_sha(),
                "train_fingerprint": fingerprint(train), "holdout_fingerprint": fingerprint(holdout),
                "n_train": len(train), "threshold": bundle["threshold"], "n_recent": n_recent,
                "recent_fingerprint": recent_fp, "suppliers": list(bundle["features"].suppliers),
                "sklearn_version": bundle["sklearn_version"], "python_version": bundle["python_version"],
                "trained_at": bundle["trained_at"]}
    bundle["identity"] = identity
    top = sorted(bundle["importance"].items(), key=lambda kv: -kv[1])[:10]

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out / "model.joblib")
    (out / "metrics.json").write_text(json.dumps(
        {"identity": identity, "metrics": metrics, "recent_metrics": recent_metrics, "acceptance": criteria,
         "releasable": releasable}, indent=2))
    (out / "reference_profile.json").write_text(json.dumps(reference_profile(bundle, train)))
    text = report(identity, metrics, criteria, top)
    (out / "validation_report.md").write_text(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text)

    for c in criteria:
        print(f"{c['id']} {'PASS' if c['passed'] else 'FAIL'}  {c['criterion']}  ->  {c['actual']}")
    print(f"\n{'RELEASABLE' if releasable else 'NOT RELEASABLE'}: artifacts written to {out}/")
    if not releasable and not allow_fail:
        sys.exit(1)
    return releasable, metrics


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="data")
    p.add_argument("--out", default="artifacts")
    p.add_argument("--allow-fail", action="store_true", help="write artifacts even if criteria fail (exit 0)")
    p.add_argument("--recent", help="labelled recent batches (JSONL) to add; 30%% is held back for AC-7")
    a = p.parse_args()
    main(a.data_dir, a.out, a.allow_fail, a.recent)
