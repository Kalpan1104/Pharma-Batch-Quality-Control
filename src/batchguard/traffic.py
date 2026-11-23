"""
Reads `gcloud run services describe SERVICE --format=json` from stdin and answers questions the
release workflows need:

    ... | python -m batchguard.traffic tagged candidate   -> revision carrying the 'candidate' tag
    ... | python -m batchguard.traffic serving            -> revision currently getting the most traffic
    ... | python -m batchguard.traffic url candidate      -> the tag's URL
"""
import json
import sys


def entries(service):
    return service.get("status", {}).get("traffic", [])


def tagged(service, tag):
    for t in entries(service):
        if t.get("tag") == tag:
            return t.get("revisionName", "")
    return ""


def tag_url(service, tag):
    for t in entries(service):
        if t.get("tag") == tag:
            return t.get("url", "")
    return ""


def serving(service):
    live = [t for t in entries(service) if t.get("percent", 0) > 0]
    if not live:
        return ""
    best = max(live, key=lambda t: t["percent"])
    return best.get("revisionName") or service.get("status", {}).get("latestReadyRevisionName", "")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    service = json.load(sys.stdin)
    cmd = argv[0]
    if cmd == "tagged":
        print(tagged(service, argv[1]))
    elif cmd == "url":
        print(tag_url(service, argv[1]))
    elif cmd == "serving":
        print(serving(service))
    else:
        sys.exit(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
