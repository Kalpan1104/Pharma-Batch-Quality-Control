"""Write report files to a local folder or a gs:// prefix."""
from pathlib import Path


def write_text(uri_base, key, text, client=None):
    if uri_base.startswith("gs://"):
        bucket, _, prefix = uri_base[len("gs://"):].partition("/")
        if client is None:
            from google.cloud import storage
            client = storage.Client()
        name = f"{prefix.strip('/')}/{key}" if prefix else key
        client.bucket(bucket).blob(name).upload_from_string(text)
        return f"gs://{bucket}/{name}"
    path = Path(uri_base) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return str(path)
