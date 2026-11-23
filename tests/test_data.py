import copy

import numpy as np
import pytest

from batchguard import config as C
from batchguard.data import fingerprint, generate, validate_batch


def test_reproducible_and_fingerprinted():
    a, b = generate(50, seed=1), generate(50, seed=1)
    assert a == b and fingerprint(a) == fingerprint(b) != fingerprint(generate(50, seed=2))


def test_all_generated_batches_pass_contract():
    for scenario in ("normal", "new_supplier", "sensor_drift"):
        assert all(validate_batch(b, require_label=True) == [] for b in generate(200, seed=5, scenario=scenario))


def test_failure_rate_and_scenarios():
    normal = np.mean([b[C.TARGET] for b in generate(3000, seed=6)])
    supplier = np.mean([b[C.TARGET] for b in generate(3000, seed=6, scenario="new_supplier")])
    assert 0.04 < normal < 0.15 and supplier > normal + 0.05


def test_sensor_drift_changes_readings_not_outcomes():
    normal, drift = generate(300, seed=9), generate(300, seed=9, scenario="sensor_drift")
    assert [b[C.TARGET] for b in normal] == [b[C.TARGET] for b in drift]
    assert np.mean([b["sensors"]["product_temp"] for b in drift]) > np.mean([b["sensors"]["product_temp"] for b in normal]) + 2.5


def _broken(**changes):
    b = copy.deepcopy(generate(1, seed=1)[0])
    for k, v in changes.items():
        if k.startswith("sensor_"):
            b["sensors"][k[7:]] = v
        else:
            b[k] = v
    return b


@pytest.mark.parametrize("batch, expected", [
    (_broken(line="L9"), "unknown line"),
    (_broken(shift="weekend"), "unknown shift"),
    (_broken(api_potency=150.0), "api_potency"),
    (_broken(sensor_product_temp=[50.0] * 10), "45 readings"),
    (_broken(sensor_tablet_weight=[250.0] * 29 + [float("nan")]), "non-numeric"),
    (_broken(sensor_inlet_temp=[500.0] * 45), "physical range"),
])
def test_contract_catches_problems(batch, expected):
    assert any(expected in p for p in validate_batch(batch))


def test_missing_fields():
    b = generate(1, seed=1)[0]
    del b["sensors"]
    assert validate_batch(b) == ["missing field 'sensors'"]
