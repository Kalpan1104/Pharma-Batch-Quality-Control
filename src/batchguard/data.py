"""
Synthetic tablet-manufacturing batches: raw-material data plus six sensor curves per batch.

Quality failures come from realistic mechanisms: over-wet granulation that drying doesn't fix,
overheating during drying, variable tablet weight, low API potency, extreme particle size.

Scenarios for monitoring:
  * "normal"        business as usual
  * "new_supplier"  raw material from SupD: coarser particles, wetter, more failures
  * "sensor_drift"  the product-temperature probe reads 3 °C high (needs recalibration);
                    the process itself is unchanged
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from batchguard import config as C


def _curve(rng, n, shape, noise):
    return shape + rng.normal(0, noise, n)


def make_batch(rng, idx, scenario="normal"):
    supplier = "SupD" if scenario == "new_supplier" and rng.random() < 0.7 else str(rng.choice(C.KNOWN_SUPPLIERS))
    line = str(rng.choice(C.LINES, p=[0.3, 0.3, 0.2, 0.2]))
    shift = str(rng.choice(C.SHIFTS, p=[0.65, 0.35]))
    d50_mean = {"SupA": 80, "SupB": 90, "SupC": 85, "SupD": 125}[supplier]
    potency = rng.normal(100.0, 0.8)
    moisture_in = rng.normal(2.0 + (0.6 if supplier == "SupD" else 0), 0.4)
    d50 = rng.normal(d50_mean, 9)
    ambient = rng.normal(45, 8)

    wetness = rng.normal(0, 1) + 0.8 * (moisture_in - 2.0) + 0.02 * (ambient - 45)
    efficiency = rng.normal(0, 1) - (0.4 if line == "L3" else 0)
    overheat = rng.random() < 0.05
    weight_var = abs(rng.normal(1.0, 0.35)) * (1.4 if shift == "night" else 1.0) * (1 + 0.004 * abs(d50 - 85))

    t30, t45 = np.linspace(0, 1, 30), np.linspace(0, 1, 45)
    mixer = _curve(rng, 30, 5 + (11 + 1.2 * wetness) * (1 - np.exp(-6 * t30)), 0.35)
    inlet = _curve(rng, 45, 25 + 45 * (1 - np.exp(-10 * t45)), 0.6)
    product = 25 + (23 + 1.5 * efficiency) * (1 - np.exp(-(4 + 0.5 * efficiency) * t45))
    if overheat:
        start = int(rng.integers(15, 35))
        product[start:start + 6] += rng.uniform(7, 11)
    product = product + rng.normal(0, 0.4, 45)
    final_rh = 14 + 3.5 * wetness - 2.5 * efficiency
    humidity = _curve(rng, 45, final_rh + (60 - final_rh) * np.exp(-5 * t45), 0.8)
    force = _curve(rng, 30, np.full(30, 15 + 0.03 * (d50 - 85)), 0.4)
    weight = _curve(rng, 30, np.full(30, 250.0), 1.5 * weight_var)

    if scenario == "sensor_drift":
        product = product + 3.0

    z = (-5.1 + 0.55 * (final_rh - 14) + 2.4 * overheat + 1.6 * (weight_var - 1)
         + 1.8 * (potency < 98.8) + 0.03 * abs(d50 - 85) + rng.normal(0, 0.3))
    failed = int(rng.random() < 1 / (1 + np.exp(-z)))

    return {
        "batch_id": f"B{idx:06d}", "line": line, "supplier": supplier, "shift": shift,
        "api_potency": round(float(potency), 2), "moisture_in": round(float(moisture_in), 3),
        "particle_d50": round(float(d50), 1), "ambient_humidity": round(float(ambient), 1),
        "sensors": {"mixer_power": np.round(mixer, 3).tolist(), "inlet_temp": np.round(inlet, 2).tolist(),
                    "product_temp": np.round(np.clip(product, 0, 110), 2).tolist(),
                    "exhaust_humidity": np.round(np.clip(humidity, 0, 100), 2).tolist(),
                    "compression_force": np.round(force, 3).tolist(), "tablet_weight": np.round(weight, 2).tolist()},
        C.TARGET: failed,
    }


def generate(n, seed=0, scenario="normal", start_id=0):
    rng = np.random.default_rng(seed)
    return [make_batch(rng, start_id + i, scenario) for i in range(n)]


def validate_batch(b, require_label=False):
    """Data contract for one batch record. Returns a list of problems."""
    problems = []
    for key in ["batch_id", "line", "supplier", "shift", *C.MATERIAL, "sensors"]:
        if key not in b:
            problems.append(f"missing field '{key}'")
    if problems:
        return problems
    if b["line"] not in C.LINES:
        problems.append(f"unknown line '{b['line']}'")
    if b["shift"] not in C.SHIFTS:
        problems.append(f"unknown shift '{b['shift']}'")
    if not isinstance(b["supplier"], str) or not b["supplier"]:
        problems.append("supplier must be a non-empty string")
    for key, (lo, hi) in C.MATERIAL.items():
        v = b[key]
        if not isinstance(v, (int, float)) or not lo <= v <= hi:
            problems.append(f"{key}={v!r} outside [{lo}, {hi}]")
    sensors = b["sensors"] if isinstance(b["sensors"], dict) else {}
    for name, length in C.SENSORS.items():
        curve = sensors.get(name)
        if not isinstance(curve, list) or len(curve) != length:
            problems.append(f"sensor '{name}' must have {length} readings")
            continue
        if not all(isinstance(x, (int, float)) and np.isfinite(x) for x in curve):
            problems.append(f"sensor '{name}' has non-numeric readings")
            continue
        lo, hi = C.SENSOR_RANGES[name]
        if min(curve) < lo or max(curve) > hi:
            problems.append(f"sensor '{name}' outside physical range [{lo}, {hi}]")
    if require_label and b.get(C.TARGET) not in (0, 1):
        problems.append(f"'{C.TARGET}' must be 0 or 1")
    return problems


def fingerprint(batches):
    """Content hash recorded with every model, so its training data can be identified exactly."""
    h = hashlib.sha256()
    for b in batches:
        h.update(json.dumps(b, sort_keys=True).encode())
    return h.hexdigest()[:16]


def write_jsonl(batches, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for b in batches:
            f.write(json.dumps(b) + "\n")


def read_jsonl(path):
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]


def write_splits(out="data"):
    out = Path(out)
    write_jsonl(generate(3000, seed=0), out / "train.jsonl")
    write_jsonl(generate(1500, seed=1000, start_id=100_000), out / "holdout.jsonl")   # fixed acceptance set
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Generate synthetic manufacturing batches")
    p.add_argument("--out", default="data")
    p.add_argument("--recent", choices=["new_supplier", "sensor_drift"],
                   help="instead, write data/recent.jsonl: 1,500 labelled recent batches from this scenario")
    a = p.parse_args()
    if a.recent:
        rows = generate(1500, seed=4242, scenario=a.recent, start_id=500_000)
        write_jsonl(rows, Path(a.out) / "recent.jsonl")
        print(f"recent ({a.recent}): {len(rows)} batches, failure rate {np.mean([b[C.TARGET] for b in rows]):.1%}")
        raise SystemExit(0)
    out = write_splits(a.out)
    for name in ["train", "holdout"]:
        rows = read_jsonl(out / f"{name}.jsonl")
        bad = sum(bool(validate_batch(b, True)) for b in rows)
        print(f"{name}: {len(rows)} batches, failure rate {np.mean([b[C.TARGET] for b in rows]):.1%}, "
              f"{bad} invalid, fingerprint {fingerprint(rows)}")
