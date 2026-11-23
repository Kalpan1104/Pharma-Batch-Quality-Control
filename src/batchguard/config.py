"""Process definition, acceptance criteria and cloud settings in one place."""
import os

# ---- process: each stage records fixed-length sensor curves (one reading per minute) ----
SENSORS = {
    "mixer_power": 30,         # granulation, kW
    "inlet_temp": 45,          # fluid-bed drying, °C
    "product_temp": 45,        # drying, °C
    "exhaust_humidity": 45,    # drying, % RH
    "compression_force": 30,   # tableting, kN
    "tablet_weight": 30,       # tableting, mg
}
LINES = ["L1", "L2", "L3", "L4"]
KNOWN_SUPPLIERS = ["SupA", "SupB", "SupC"]   # suppliers in the original training data
SHIFTS = ["day", "night"]
MATERIAL = {                   # raw-material certificate of analysis, with valid ranges
    "api_potency": (90.0, 110.0),     # % label claim
    "moisture_in": (0.0, 8.0),        # % w/w
    "particle_d50": (10.0, 300.0),    # µm
    "ambient_humidity": (10.0, 90.0), # % RH
}
SENSOR_RANGES = {
    "mixer_power": (0, 40), "inlet_temp": (0, 120), "product_temp": (0, 110),
    "exhaust_humidity": (0, 100), "compression_force": (0, 60), "tablet_weight": (100, 400),
}
TARGET = "failed_qc"

# ---- acceptance criteria (a release must meet ALL of them on the fixed holdout) ----
TARGET_RECALL = 0.85          # threshold is chosen on validation data to catch this share of bad batches
MIN_RECALL = 0.80             # ...and must still catch at least this many on the holdout
MIN_PRECISION = 0.25          # each unnecessary hold costs production time
MIN_AUC = 0.85
MIN_LINE_RECALL = 0.65        # no production line may be left blind
MAX_P95_LATENCY_MS = 100.0

# ---- monitoring ----
PSI_WARN, PSI_ALERT = 0.10, 0.25
HOLD_RATE_SHIFT_ALERT = 0.10
UNKNOWN_SUPPLIER_ALERT = 0.05
MIN_MONITOR_ROWS = 30

# ---- where things go (overridden by environment variables in the cloud) ----
MODEL_PATH = os.getenv("MODEL_PATH", "artifacts/model.joblib")
PREDICTION_SINK = os.getenv("PREDICTION_SINK", "jsonl:logs/predictions.jsonl")
AUDIT_SINK = os.getenv("AUDIT_SINK", "jsonl:logs/audit.jsonl")
REPORTS_URI = os.getenv("REPORTS_URI", "reports")
MODEL_VERSION = os.getenv("MODEL_VERSION", "dev")
MONITOR_WINDOW_HOURS = int(os.getenv("MONITOR_WINDOW_HOURS", "24"))
