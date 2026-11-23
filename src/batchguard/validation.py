"""
Acceptance criteria and the model validation report.

In regulated manufacturing a model is released against criteria agreed in advance, not just
because it beats the previous one. Every criterion below must pass, and the report is kept
with the release (container image label + audit log).
"""
from batchguard import config as C


def acceptance(metrics, reproducible, recent=None):
    lines = metrics["line_recall"]
    worst_line = min(lines, key=lines.get)
    criteria = [
        {"id": "AC-1", "criterion": f"Recall on failed batches ≥ {C.MIN_RECALL}", "actual": metrics["recall"],
         "passed": metrics["recall"] >= C.MIN_RECALL},
        {"id": "AC-2", "criterion": f"Precision of holds ≥ {C.MIN_PRECISION}", "actual": metrics["precision"],
         "passed": metrics["precision"] >= C.MIN_PRECISION},
        {"id": "AC-3", "criterion": f"ROC AUC ≥ {C.MIN_AUC}", "actual": metrics["auc"], "passed": metrics["auc"] >= C.MIN_AUC},
        {"id": "AC-4", "criterion": f"Recall on every production line ≥ {C.MIN_LINE_RECALL}",
         "actual": f"{worst_line}: {lines[worst_line]}", "passed": lines[worst_line] >= C.MIN_LINE_RECALL},
        {"id": "AC-5", "criterion": f"p95 single-batch latency ≤ {C.MAX_P95_LATENCY_MS} ms",
         "actual": metrics["p95_latency_ms"], "passed": metrics["p95_latency_ms"] <= C.MAX_P95_LATENCY_MS},
        {"id": "AC-6", "criterion": "Retraining on the same data and seed reproduces identical predictions",
         "actual": "identical" if reproducible else "differs", "passed": bool(reproducible)},
    ]
    if recent is not None:  # retraining after a process or material change must also work on recent batches
        criteria.append({"id": "AC-7", "criterion": f"Recall on held-back recent batches ≥ {C.MIN_RECALL} and AUC ≥ {C.MIN_AUC}",
                         "actual": f"recall {recent['recall']}, AUC {recent['auc']}",
                         "passed": recent["recall"] >= C.MIN_RECALL and recent["auc"] >= C.MIN_AUC})
    return criteria


def report(identity, metrics, criteria, top_features):
    recent_line = (f"| Recent batches added | {identity['n_recent']} (fingerprint `{identity['recent_fingerprint']}`) |\n"
                   if identity.get("n_recent") else "")
    ok = all(c["passed"] for c in criteria)
    rows = "\n".join(f"| {c['id']} | {c['criterion']} | {c['actual']} | {'✅ pass' if c['passed'] else '❌ fail'} |"
                     for c in criteria)
    lines = "\n".join(f"| {k} | {v} |" for k, v in metrics["line_recall"].items())
    feats = "\n".join(f"| {f} | {v:.4f} |" for f, v in top_features)
    return f"""# Model Validation Report: BatchGuard {identity['model_version']}

**Result: {'✅ ALL ACCEPTANCE CRITERIA MET' if ok else '❌ NOT RELEASABLE'}**

## Identity
| Item | Value |
|---|---|
| Model version | `{identity['model_version']}` |
| Source commit | `{identity['git_sha']}` |
| Training data fingerprint | `{identity['train_fingerprint']}` ({identity['n_train']} batches) |
| Acceptance data fingerprint | `{identity['holdout_fingerprint']}` ({metrics['n']} batches) |
{recent_line}| Operating threshold | {identity['threshold']:.4f} (chosen on validation data for {C.TARGET_RECALL:.0%} recall) |
| scikit-learn / Python | {identity['sklearn_version']} / {identity['python_version']} |
| Trained at (UTC) | {identity['trained_at']} |

## Intended use
Flags tablet batches at risk of failing quality-control release testing, after compression and
before release testing, so QA can review and hold them. It **supports** the QA decision; it does
not replace release testing.

## Acceptance criteria (fixed holdout)
| ID | Criterion | Result | Status |
|---|---|---|---|
{rows}

## Holdout performance
- Failure rate: {metrics['failure_rate']:.1%} · batches held for review: {metrics['hold_rate']:.1%}
- Failed batches caught: {metrics['recall']:.1%} · missed: {metrics['missed_failures']}
- Holds that were real failures (precision): {metrics['precision']:.1%}

| Line | Recall |
|---|---|
{lines}

## Most influential inputs (permutation importance, AUC drop)
| Feature | Importance |
|---|---|
{feats}

## Known limitations
- Trained on suppliers {', '.join(identity['suppliers'])}; batches from other suppliers are scored, and monitoring flags them.
- Sensor calibration drift changes inputs without changing the process. Monitoring separates the two.
- Synthetic process data; a real deployment requires validation on historical batch records.

## Approval
Release approval is recorded in the audit log (`audit_log` table) when the release is promoted.
"""
