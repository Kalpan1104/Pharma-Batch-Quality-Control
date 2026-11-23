"""
Append-only tables for predictions and the audit trail.

    jsonl:logs/predictions.jsonl          local file
    bigquery:PROJECT.DATASET.TABLE        BigQuery
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

JSON_FIELDS = ("features", "details")


def _parse(row):
    for f in JSON_FIELDS:
        if isinstance(row.get(f), str):
            row[f] = json.loads(row[f])
    if hasattr(row.get("ts"), "isoformat"):
        row["ts"] = row["ts"].isoformat()
    return row


class JsonlSink:
    def __init__(self, path):
        self.path = Path(path)

    def write(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            for r in rows:
                f.write(json.dumps(r, default=str) + "\n")

    def _all(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def read_since(self, hours):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return [r for r in self._all() if datetime.fromisoformat(r["ts"]) >= cutoff]

    def latest(self, field, value):
        best = None
        for r in self._all():                      # later lines win ties, matching append order
            if r.get(field) == value and (best is None or r["ts"] >= best["ts"]):
                best = r
        return best


class BigQuerySink:
    def __init__(self, table, client=None):
        if client is None:
            from google.cloud import bigquery
            client = bigquery.Client()
        self.client, self.table = client, table

    def write(self, rows):
        payload = [{**r, **{f: json.dumps(r[f]) for f in JSON_FIELDS if f in r}} for r in rows]
        errors = self.client.insert_rows_json(self.table, payload)
        if errors:
            raise RuntimeError(f"BigQuery insert failed: {errors[:3]}")

    def _query(self, sql, params):
        from google.cloud import bigquery
        cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter(k, t, v) for k, t, v in params])
        return [_parse(dict(r.items())) for r in self.client.query(sql, job_config=cfg).result()]

    def read_since(self, hours):
        sql = f"SELECT * FROM `{self.table}` WHERE ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @hours HOUR)"
        return self._query(sql, [("hours", "INT64", int(hours))])

    def latest(self, field, value):
        if not field.isidentifier():
            raise ValueError("bad field name")
        rows = self._query(f"SELECT * FROM `{self.table}` WHERE {field} = @value ORDER BY ts DESC LIMIT 1",
                           [("value", "STRING", value)])
        return rows[0] if rows else None


def open_sink(spec, client=None):
    kind, _, target = spec.partition(":")
    if kind == "jsonl":
        return JsonlSink(target)
    if kind == "bigquery":
        return BigQuerySink(target, client=client)
    raise ValueError(f"unknown sink '{spec}' (use jsonl:PATH or bigquery:PROJECT.DATASET.TABLE)")
