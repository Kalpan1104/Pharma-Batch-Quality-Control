"""
Send batches to the deployed service (identity token from gcloud) or a local server.

    python scripts/send_batches.py --n 300                         # normal production
    python scripts/send_batches.py --n 300 --scenario new_supplier # material change
    python scripts/send_batches.py --n 300 --scenario sensor_drift # drifting temperature probe
    python scripts/send_batches.py --url http://localhost:8080 --n 100
"""
import argparse
import subprocess
import sys
import time
from collections import Counter

import requests

from batchguard import config as C
from batchguard.data import generate


def gcloud(*args):
    return subprocess.check_output(["gcloud", *args], text=True).strip()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url")
    p.add_argument("--service", default="batchguard")
    p.add_argument("--region", default="us-east1")
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--scenario", choices=["normal", "new_supplier", "sensor_drift"], default="normal")
    a = p.parse_args()

    url, headers = a.url, {}
    if not url:
        url = gcloud("run", "services", "describe", a.service, f"--region={a.region}", "--format=value(status.url)")
    if "localhost" not in url and "127.0.0.1" not in url:
        headers["Authorization"] = f"Bearer {gcloud('auth', 'print-identity-token')}"

    rows = generate(a.n, seed=int(time.time()) % 100_000, scenario=a.scenario, start_id=800_000)
    for r in rows:
        r.pop(C.TARGET)
    decisions, revisions = Counter(), Counter()
    for i in range(0, len(rows), a.batch):
        r = requests.post(f"{url}/v1/predict", json={"batches": rows[i:i + a.batch]}, headers=headers, timeout=120)
        if r.status_code != 200:
            sys.exit(f"HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        revisions[body["revision"]] += len(body["results"])
        decisions.update(x["decision"] for x in body["results"])
    total = sum(decisions.values())
    print(f"sent {total} {a.scenario} batches to {url}")
    print("  decisions:", {k: f"{v} ({v / total:.0%})" for k, v in decisions.items()})
    print("  served by revision:", dict(revisions))


if __name__ == "__main__":
    main()
