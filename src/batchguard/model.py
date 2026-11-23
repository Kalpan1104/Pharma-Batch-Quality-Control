"""Model, operating threshold, evaluation and explanations."""
import platform
import time
from datetime import datetime, timezone

import numpy as np
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from batchguard import config as C
from batchguard.features import FeatureBuilder, describe

CATEGORICAL = ["line", "shift", "supplier"]
SEED = 7


def build_pipeline(numeric):
    pre = ColumnTransformer([
        ("num", "passthrough", numeric),
                ("cat", OneHotEncoder(categories=[C.LINES, C.SHIFTS, [*C.KNOWN_SUPPLIERS, "SupD", "other"]],
                              handle_unknown="ignore", sparse_output=False), CATEGORICAL),
    ])
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                         l2_regularization=1.0, random_state=SEED)
    return Pipeline([("pre", pre), ("clf", clf)])


def choose_threshold(y, p, target_recall=C.TARGET_RECALL):
    """Highest threshold that still catches `target_recall` of bad batches (fewest unnecessary holds)."""
    order = np.sort(np.unique(p))[::-1]
    for t in order:
        if recall_score(y, p >= t, zero_division=0) >= target_recall:
            return float(t)
    return float(order[-1])


def fit(train_batches, val_batches):
    fb = FeatureBuilder().fit(train_batches)
    X_tr, X_val = fb.transform(train_batches), fb.transform(val_batches)
    y_tr = np.array([b[C.TARGET] for b in train_batches])
    y_val = np.array([b[C.TARGET] for b in val_batches])
    numeric = [c for c in X_tr.columns if c not in CATEGORICAL]
    pipe = build_pipeline(numeric).fit(X_tr, y_tr)
    p_val = pipe.predict_proba(X_val)[:, 1]
    imp = permutation_importance(pipe, X_val, y_val, scoring="roc_auc", n_repeats=3, random_state=SEED)
    importance = {c: max(float(v), 0.0) for c, v in zip(X_val.columns, imp.importances_mean)}
    return {
        "features": fb, "pipeline": pipe, "threshold": choose_threshold(y_val, p_val),
        "importance": importance, "sklearn_version": sklearn.__version__,
        "python_version": platform.python_version(),
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def predict_risk(bundle, batches):
    X = bundle["features"].transform(batches)
    return bundle["pipeline"].predict_proba(X)[:, 1], X


def explain(bundle, X, top=3):
    """Top factors for each batch: important features that sit furthest from normal."""
    z = bundle["features"].deviations(X)
    weights = np.array([bundle["importance"].get(c, 0.0) for c in z.columns])
    if weights.sum() == 0:
        weights = np.ones_like(weights)
    out = []
    for _, row in z.iterrows():
        score = np.abs(row.to_numpy()) * weights
        idx = np.argsort(-score)[:top]
        out.append([describe(z.columns[i], float(row.iloc[i])) for i in idx if score[i] > 0])
    return out


def evaluate(bundle, batches):
    y = np.array([b[C.TARGET] for b in batches])
    p, _ = predict_risk(bundle, batches)
    hold = p >= bundle["threshold"]
    lines = np.array([b["line"] for b in batches])
    line_recall = {ln: round(float(recall_score(y[lines == ln], hold[lines == ln], zero_division=0)), 4)
                   for ln in C.LINES if (y[lines == ln] == 1).any()}
    return {
        "n": len(y), "failure_rate": round(float(y.mean()), 4),
        "auc": round(float(roc_auc_score(y, p)), 4),
        "recall": round(float(recall_score(y, hold, zero_division=0)), 4),
        "precision": round(float(precision_score(y, hold, zero_division=0)), 4),
        "hold_rate": round(float(hold.mean()), 4),
        "missed_failures": int(((y == 1) & ~hold).sum()),
        "line_recall": line_recall,
        "p95_latency_ms": p95_latency_ms(bundle, batches[:100]),
    }


def p95_latency_ms(bundle, batches):
    predict_risk(bundle, batches[:2])
    times = []
    for b in batches:
        start = time.perf_counter()
        predict_risk(bundle, [b])
        times.append((time.perf_counter() - start) * 1000)
    return round(float(np.percentile(times, 95)), 2)


def share_in_bins(values, edges):
    e = np.array(edges, dtype=float)
    e[0], e[-1] = -np.inf, np.inf
    return (np.histogram(np.asarray(values, float), e)[0] / max(len(values), 1)).tolist()


MONITORED = ["moisture_in", "particle_d50", "api_potency", "product_temp__max", "product_temp__final",
             "exhaust_humidity__final", "mixer_power__final", "tablet_weight__cv"]


def reference_profile(bundle, batches):
    p, X = predict_risk(bundle, batches)
    prof = {"hold_rate": float(np.mean(p >= bundle["threshold"])), "mean_risk": float(p.mean()),
            "suppliers": list(bundle["features"].suppliers), "features": {}}
    for col in MONITORED:
        edges = np.unique(np.quantile(X[col], np.linspace(0, 1, 11))).tolist()
        prof["features"][col] = {"edges": edges, "share": share_in_bins(X[col], edges)}
    risk_edges = np.unique(np.quantile(p, np.linspace(0, 1, 11))).tolist()
    prof["risk"] = {"edges": risk_edges, "share": share_in_bins(p, risk_edges)}
    return prof
