"""
Operational qualification (OQ): a fixed set of checks run against a freshly deployed revision
BEFORE it receives any traffic, and again during the canary.

    python -m batchguard.oq --url https://candidate---batchguard-xxxx.run.app --token "$TOKEN" \
        --expect-version abc123 --out oq_results.json
"""
import argparse
import copy
import json
import sys
import time

import numpy as np

from batchguard import config as C
from batchguard.data import generate


def reference_batches():
    """A typical batch, the same batch made obviously bad, and one that breaks the contract."""
    good = generate(1, seed=2024, start_id=990_000)[0]
    good.pop(C.TARGET, None)
    good["batch_id"] = "OQ-GOOD"
    bad = copy.deepcopy(good)
    bad["batch_id"] = "OQ-BAD"
    bad["api_potency"] = 96.5
    s = bad["sensors"]
    s["exhaust_humidity"] = [round(v + 12, 2) for v in s["exhaust_humidity"]]            # still wet at the end
    s["product_temp"] = [round(v + (10 if 20 <= i < 27 else 0), 2) for i, v in enumerate(s["product_temp"])]
    s["tablet_weight"] = [round(250 + 6 * np.sin(i), 2) for i in range(30)]               # erratic weights
    broken = copy.deepcopy(good)
    broken["batch_id"] = "OQ-BROKEN"
    broken["sensors"]["product_temp"] = broken["sensors"]["product_temp"][:10]
    return good, bad, broken


def run(call, expect_version=None, latency_requests=20, max_p95_ms=1500):
    """`call(method, path, json_body) -> (status, body)`; works with HTTP or a Flask test client."""
    good, bad, broken = reference_batches()
    checks = []

    def check(cid, desc, ok, detail=""):
        checks.append({"id": cid, "check": desc, "passed": bool(ok), "detail": detail})

    status, body = call("GET", "/health", None)
    check("OQ-1", "Health endpoint responds", status == 200 and body.get("status") == "ok", body)
    if expect_version:
        check("OQ-2", f"Serves the expected model version ({expect_version})",
              body.get("model_version") == expect_version, body.get("model_version"))
    status, body = call("GET", "/v1/model", None)
    check("OQ-3", "Model reports all acceptance criteria met", status == 200 and body.get("releasable") is True,
          body.get("releasable"))
    status, body = call("POST", "/v1/predict", {"batches": [good, bad]})
    ok = status == 200 and len(body.get("results", [])) == 2
    res = {r["batch_id"]: r for r in body.get("results", [])} if ok else {}
    check("OQ-4", "Typical batch is allowed to proceed", res.get("OQ-GOOD", {}).get("decision") == "proceed",
          res.get("OQ-GOOD", {}).get("risk"))
    check("OQ-5", "Wet, overheated, low-potency batch is held with reasons",
          res.get("OQ-BAD", {}).get("decision") == "hold_for_review" and len(res.get("OQ-BAD", {}).get("top_factors", [])) > 0,
          res.get("OQ-BAD", {}).get("top_factors"))
    status, body = call("POST", "/v1/predict", {"batches": [broken]})
    check("OQ-6", "Batch with a truncated sensor curve is rejected (422)", status == 422, status)
    status, _ = call("POST", "/v1/predict", {"wrong": True})
    check("OQ-7", "Malformed request is rejected (400)", status == 400, status)
    times = []
    for _ in range(latency_requests):
        start = time.perf_counter()
        call("POST", "/v1/predict", {"batches": [good]})
        times.append((time.perf_counter() - start) * 1000)
    p95 = float(np.percentile(times, 95))
    check("OQ-8", f"p95 latency over {latency_requests} requests ≤ {max_p95_ms} ms", p95 <= max_p95_ms, round(p95, 1))
    return checks


def http_caller(base_url, token=None):
    import requests
    session = requests.Session()
    if token:
        session.headers["Authorization"] = f"Bearer {token}"

    def call(method, path, body):
        r = session.request(method, base_url.rstrip("/") + path, json=body, timeout=60)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"raw": r.text[:300]}
    return call


def to_markdown(checks, url):
    rows = "\n".join(f"| {c['id']} | {c['check']} | {'✅' if c['passed'] else '❌'} | {json.dumps(c['detail'], default=str)[:80]} |"
                     for c in checks)
    return f"## Operational qualification: `{url}`\n\n| ID | Check | Result | Detail |\n|---|---|---|---|\n{rows}\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--token")
    p.add_argument("--expect-version")
    p.add_argument("--out", default="oq_results.json")
    a = p.parse_args()
    checks = run(http_caller(a.url, a.token), a.expect_version)
    passed = all(c["passed"] for c in checks)
    with open(a.out, "w") as f:
        json.dump({"url": a.url, "passed": passed, "checks": checks}, f, indent=2, default=str)
    md = to_markdown(checks, a.url)
    print(md)
    import os
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
