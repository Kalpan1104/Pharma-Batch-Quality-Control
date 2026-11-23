import pytest

from batchguard import config as C
from batchguard.data import generate
from batchguard.model import fit


@pytest.fixture(scope="session")
def batches():
    return generate(1600, seed=3)


@pytest.fixture(scope="session")
def holdout():
    return generate(600, seed=4, start_id=50_000)


@pytest.fixture(scope="session")
def bundle(batches):
    b = fit(batches[:1200], batches[1200:])
    b["identity"] = {"model_version": "test-v1", "git_sha": "abc", "suppliers": list(b["features"].suppliers)}
    return b


@pytest.fixture
def unlabeled():
    rows = generate(5, seed=8, start_id=70_000)
    for r in rows:
        r.pop(C.TARGET)
    return rows
