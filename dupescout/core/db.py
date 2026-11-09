"""SQLite-cache voor hashes, quarantaine-registratie, ordenen-journal en logboek."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from typing import Any, Optional

from .models import FileInfo


def app_home() -> str:
    env = os.environ.get("DUPESCOUT_HOME")
    if env:
        base = env
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/DupeScout")
    else:
        base = os.path.expanduser("~/.local/share/dupescout")
    os.makedirs(base, exist_ok=True)
    return base


SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
    volume_id TEXT NOT NULL,
    relpath   TEXT NOT NULL,
    size      INTEGER NOT NULL,
    mtime_ns  INTEGER NOT NULL,
    partial   TEXT,
    full      TEXT,
    phash     INTEGER,
    width     INTEGER,
    height    INTEGER,
    taken     TEXT,
    vsig      TEXT,
    PRIMARY KEY(volume_id, relpath)
);
CREATE TABLE IF NOT EXISTS quarantine_mounts(mount_point TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS journal(
    batch_id TEXT NOT NULL,
    seq      INTEGER NOT NULL,
    op       TEXT NOT NULL,
    src      TEXT NOT NULL,
    dst      TEXT NOT NULL,
    extra    TEXT,
    state    TEXT NOT NULL DEFAULT 'done',
    ts       REAL NOT NULL,
    PRIMARY KEY(batch_id, seq)
);
CREATE TABLE IF NOT EXISTS actions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);
"""

_FIELDS = ("partial", "full", "phash", "width", "height", "taken", "vsig")


def _to_signed(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


def _to_unsigned(v: int) -> int:
    return v + (1 << 64) if v < 0 else v


class Cache:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.path.join(app_home(), "cache.db")
        self._lock = threading.RLock()
        self._pending = 0
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ---- bestandscache -------------------------------------------------
    def lookup(self, f: FileInfo) -> dict[str, Any]:
        """Geldige cachevelden voor dit bestand (leeg als grootte/mtime afwijken)."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM files WHERE volume_id=? AND relpath=?", (f.volume_id, f.relpath)
            ).fetchone()
        if row is None or row["size"] != f.size or row["mtime_ns"] != f.mtime_ns:
            return {}
        d = {k: row[k] for k in _FIELDS if row[k] is not None}
        if "phash" in d:
            d["phash"] = _to_unsigned(d["phash"])
        if "vsig" in d:
            d["vsig"] = json.loads(d["vsig"])
        return d

    def store(self, f: FileInfo, **fields: Any) -> None:
        if "phash" in fields and fields["phash"] is not None:
            fields["phash"] = _to_signed(fields["phash"])
        if "vsig" in fields and fields["vsig"] is not None:
            fields["vsig"] = json.dumps(fields["vsig"])
        with self._lock:
            row = self._db.execute(
                "SELECT size, mtime_ns FROM files WHERE volume_id=? AND relpath=?", (f.volume_id, f.relpath)
            ).fetchone()
            if row is None or row["size"] != f.size or row["mtime_ns"] != f.mtime_ns:
                self._db.execute("DELETE FROM files WHERE volume_id=? AND relpath=?", (f.volume_id, f.relpath))
                self._db.execute(
                    "INSERT INTO files(volume_id, relpath, size, mtime_ns) VALUES(?,?,?,?)",
                    (f.volume_id, f.relpath, f.size, f.mtime_ns),
                )
            for k, v in fields.items():
                if k not in _FIELDS:
                    raise KeyError(k)
                self._db.execute(
                    f"UPDATE files SET {k}=? WHERE volume_id=? AND relpath=?", (v, f.volume_id, f.relpath)
                )
            self._pending += 1
            if self._pending >= 500:
                self._commit()

    def forget(self, volume_id: str, relpath: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM files WHERE volume_id=? AND relpath=?", (volume_id, relpath))
            self._commit()

    def _commit(self) -> None:
        self._db.commit()
        self._pending = 0

    def flush(self) -> None:
        with self._lock:
            self._commit()

    def close(self) -> None:
        with self._lock:
            self._commit()
            self._db.close()

    # ---- quarantaine-registratie --------------------------------------
    def register_quarantine_mount(self, mount_point: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO quarantine_mounts VALUES(?)", (mount_point,))
            self._commit()

    def quarantine_mounts(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT mount_point FROM quarantine_mounts ORDER BY 1")]

    # ---- journal (ordenen) --------------------------------------------
    def journal_add(self, batch_id: str, seq: int, op: str, src: str, dst: str, extra: Any = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO journal(batch_id, seq, op, src, dst, extra, state, ts) VALUES(?,?,?,?,?,?, 'done', ?)",
                (batch_id, seq, op, src, dst, json.dumps(extra) if extra is not None else None, time.time()),
            )
            self._pending += 1
            if self._pending >= 100:
                self._commit()

    def journal_entries(self, batch_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM journal WHERE batch_id=? ORDER BY seq", (batch_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["extra"] = json.loads(d["extra"]) if d["extra"] else None
            out.append(d)
        return out

    def journal_set_state(self, batch_id: str, seq: int, state: str) -> None:
        with self._lock:
            self._db.execute("UPDATE journal SET state=? WHERE batch_id=? AND seq=?", (state, batch_id, seq))
            self._commit()

    def journal_batches(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT batch_id, COUNT(*) AS n, MIN(ts) AS ts, "
                "SUM(CASE WHEN state='done' THEN 1 ELSE 0 END) AS active "
                "FROM journal GROUP BY batch_id ORDER BY ts DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- logboek --------------------------------------------------------
    def log(self, action: str, detail: Any = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO actions(ts, action, detail) VALUES(?,?,?)",
                (time.time(), action, json.dumps(detail, ensure_ascii=False) if detail is not None else None),
            )
            self._commit()

    def logs(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
