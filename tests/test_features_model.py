import numpy as np

from batchguard import config as C
from batchguard.features import FeatureBuilder
from batchguard.model import choose_threshold, evaluate, explain, fit, predict_risk, reference_profile
from batchguard.validation import acceptance


def test_features_identical_in_batch_and_single_mode(bundle, holdout):
    """Train/serve parity: features for a batch scored alone equal those computed in bulk."""
    fb = bundle["features"]
    bulk = fb.transform(holdout[:20])
    for i in range(20):
        single = fb.transform([holdout[i]])
        assert single.iloc[0].equals(bulk.iloc[i])


def test_unknown_supplier_maps_to_other(bundle, holdout):
    b = dict(holdout[0], supplier="SupZ")
    assert bundle["features"].transform([b])["supplier"].iloc[0] == "other"


def test_builder_must_be_fitted(holdout):
    import pytest
    with pytest.raises(RuntimeError):
        FeatureBuilder().transform(holdout[:1])


def test_threshold_meets_target_recall():
    y = np.array([0, 0, 1, 0, 1, 1, 0, 1])
    p = np.array([0.1, 0.2, 0.9, 0.3, 0.8, 0.4, 0.35, 0.7])
    t = choose_threshold(y, p, target_recall=0.75)
    assert ((p >= t) & (y == 1)).sum() / y.sum() >= 0.75 and t == 0.7


def test_model_quality(bundle, holdout):
    m = evaluate(bundle, holdout)
    assert m["auc"] > 0.85 and m["recall"] >= 0.75
    assert set(m["line_recall"]) <= set(C.LINES)


def test_training_is_reproducible(batches, holdout):
    a, b = fit(batches[:800], batches[800:1100]), fit(batches[:800], batches[800:1100])
    assert np.array_equal(predict_risk(a, holdout)[0], predict_risk(b, holdout)[0])


def test_explanations_name_real_causes(bundle, holdout):
    risk, X = predict_risk(bundle, holdout)
    reasons = explain(bundle, X.iloc[[int(risk.argmax())]])[0]
    assert 1 <= len(reasons) <= 3 and all("σ" in r for r in reasons)


def test_reference_profile(bundle, batches):
    prof = reference_profile(bundle, batches[:500])
    assert abs(sum(prof["risk"]["share"]) - 1) < 1e-9 and "particle_d50" in prof["features"]


def test_acceptance_criteria_logic():
    m = {"recall": 0.9, "precision": 0.3, "auc": 0.9, "line_recall": {"L1": 0.9, "L2": 0.5}, "p95_latency_ms": 5}
    crit = {c["id"]: c["passed"] for c in acceptance(m, reproducible=True)}
    assert crit["AC-1"] and crit["AC-3"] and not crit["AC-4"]
    assert not {c["id"]: c["passed"] for c in acceptance(m, reproducible=False)}["AC-6"]
    with_recent = acceptance(m, True, recent={"recall": 0.6, "auc": 0.9})
    assert with_recent[-1]["id"] == "AC-7" and not with_recent[-1]["passed"]
