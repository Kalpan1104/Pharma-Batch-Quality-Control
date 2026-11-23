"""
Append-only audit trail of every release event.

    python -m batchguard.audit record --event candidate_deployed --revision R --image I --detail key=value
    python -m batchguard.audit last --event promoted        # prints the latest matching entry as JSON

Events used by the workflows: candidate_deployed, oq_passed, canary_started, promoted, rolled_back.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from batchguard import config as C
from batchguard.sinks import open_sink


def record(event, sink=None, **fields):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "event": event,
        "actor": fields.pop("actor", None) or os.getenv("GITHUB_ACTOR", os.getenv("USER", "unknown")),
        "git_sha": fields.pop("git_sha", None) or os.getenv("GITHUB_SHA", ""),
        "run_url": fields.pop("run_url", None) or (
            f"{os.getenv('GITHUB_SERVER_URL')}/{os.getenv('GITHUB_REPOSITORY')}/actions/runs/{os.getenv('GITHUB_RUN_ID')}"
            if os.getenv("GITHUB_RUN_ID") else ""),
        "image": fields.pop("image", ""), "revision": fields.pop("revision", ""),
        "model_version": fields.pop("model_version", ""), "details": fields.pop("details", {}),
    }
    entry["details"].update(fields)
    (sink or open_sink(C.AUDIT_SINK)).write([entry])
    return entry


def last(event, sink=None):
    return (sink or open_sink(C.AUDIT_SINK)).latest("event", event)


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--event", required=True)
    for f in ("revision", "image", "model_version", "actor"):
        r.add_argument(f"--{f.replace('_', '-')}", dest=f, default="")
    r.add_argument("--detail", action="append", default=[], help="key=value, repeatable")
    r.add_argument("--details-file", help="JSON file merged into details (e.g. artifacts/metrics.json)")
    q = sub.add_parser("last")
    q.add_argument("--event", required=True)
    q.add_argument("--field", help="print only this field (or details.<key>)")
    a = p.parse_args(argv)

    if a.cmd == "record":
        details = dict(kv.split("=", 1) for kv in a.detail)
        if a.details_file:
            details.update(json.loads(open(a.details_file).read()))
        entry = record(a.event, revision=a.revision, image=a.image, model_version=a.model_version,
                       actor=a.actor or None, details=details)
        print(json.dumps(entry))
    else:
        entry = last(a.event)
        if entry is None:
            sys.exit(f"no '{a.event}' entry in the audit log")
        if a.field:
            value = entry
            for part in a.field.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            print(value if value is not None else "")
        else:
            print(json.dumps(entry, indent=2))


if __name__ == "__main__":
    main()
