"""
Turns raw batch records (material data + sensor curves) into model features.

The SAME FeatureBuilder object is fitted at training time, saved inside the model bundle and
used by the API, so training and serving can't compute features differently
(tests/test_features.py checks this).

"Golden batch" = the average curve of batches that passed QC. Deviation from it is the most
informative signal, and also what operators understand.
"""
import numpy as np
import pandas as pd

from batchguard import config as C

STATS = ["mean", "max", "min", "std", "final", "slope", "dev", "max_dev"]
READABLE = {
    "mixer_power": "granulation mixer power", "inlet_temp": "dryer inlet temperature",
    "product_temp": "product temperature during drying", "exhaust_humidity": "dryer exhaust humidity",
    "compression_force": "compression force", "tablet_weight": "tablet weight",
    "api_potency": "API potency", "moisture_in": "incoming moisture", "particle_d50": "particle size (d50)",
    "ambient_humidity": "ambient humidity",
}
STAT_WORDS = {"mean": "average", "max": "peak", "min": "minimum", "std": "variability", "final": "end-of-stage value",
              "slope": "trend", "dev": "average deviation from golden batch", "max_dev": "largest deviation from golden batch",
              "above_55": "minutes above 55 °C", "cv": "relative variability"}


class FeatureBuilder:
    def __init__(self):
        self.golden = None
        self.ref_mean = None
        self.ref_std = None
        self.suppliers = []

    def fit(self, batches):
        self.suppliers = sorted({b["supplier"] for b in batches})
        passed = [b for b in batches if b.get(C.TARGET) == 0] or batches
        self.golden = {s: np.mean([b["sensors"][s] for b in passed], axis=0) for s in C.SENSORS}
        ref = self._numeric(passed)
        self.ref_mean, self.ref_std = ref.mean(), ref.std().replace(0, 1.0)
        return self

    def _numeric(self, batches):
        rows = []
        for b in batches:
            row = {k: float(b[k]) for k in C.MATERIAL}
            for s in C.SENSORS:
                x = np.asarray(b["sensors"][s], dtype=float)
                d = x - self.golden[s]
                row.update({
                    f"{s}__mean": x.mean(), f"{s}__max": x.max(), f"{s}__min": x.min(), f"{s}__std": x.std(),
                    f"{s}__final": x[-3:].mean(), f"{s}__slope": np.polyfit(np.arange(len(x)), x, 1)[0],
                    f"{s}__dev": np.abs(d).mean(), f"{s}__max_dev": np.abs(d).max(),
                })
            row["product_temp__above_55"] = float((np.asarray(b["sensors"]["product_temp"]) > 55).sum())
            w = np.asarray(b["sensors"]["tablet_weight"], dtype=float)
            row["tablet_weight__cv"] = w.std() / w.mean()
            rows.append(row)
        return pd.DataFrame(rows)

    def transform(self, batches):
        if self.golden is None:
            raise RuntimeError("FeatureBuilder must be fitted first")
        X = self._numeric(batches)
        X["line"] = [b["line"] for b in batches]
        X["shift"] = [b["shift"] for b in batches]
        known = getattr(self, "suppliers", None) or C.KNOWN_SUPPLIERS   # bundles saved before suppliers were learned
        X["supplier"] = [b["supplier"] if b["supplier"] in known else "other" for b in batches]
        return X

    def deviations(self, X):
        """z-scores against batches that passed QC (used for explanations and drift checks)."""
        cols = list(self.ref_mean.index)
        return (X[cols] - self.ref_mean) / self.ref_std


def describe(feature, z):
    """'product temperature during drying: peak 3.1σ above normal'"""
    base, _, stat = feature.partition("__")
    what = READABLE.get(base, base)
    direction = "above" if z > 0 else "below"
    return f"{what}: {STAT_WORDS.get(stat, 'value') if stat else 'value'} {abs(z):.1f}σ {direction} normal"
