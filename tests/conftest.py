import os

import pytest

from dupescout.core import drives
from dupescout.core.db import Cache


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Isoleer app-data en quarantaine per test (de tmp-map staat anders op '/')."""
    monkeypatch.setenv("DUPESCOUT_HOME", str(tmp_path / "home"))
    drives.clear_cache()
    from dupescout.core import quarantine as q

    real = q.quarantine_base
    monkeypatch.setattr(q, "quarantine_base", lambda p: str(tmp_path) if os.path.realpath(p).startswith(str(tmp_path.resolve())) else real(p))
    yield


@pytest.fixture
def cache(tmp_path):
    c = Cache(str(tmp_path / "cache.db"))
    yield c
    c.close()


@pytest.fixture
def make(tmp_path):
    base = tmp_path / "data"
    base.mkdir()

    def _make(rel: str, content: bytes = b"x", mtime: float | None = None):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return str(p)

    _make.base = str(base)
    return _make
