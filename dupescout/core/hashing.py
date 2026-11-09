"""Bestandshashes (BLAKE2b) en byte-voor-byte vergelijking."""

from __future__ import annotations

import hashlib
import os
from typing import Optional

from .jobs import JobControl, checkpoint

CHUNK = 1024 * 1024
PARTIAL = 64 * 1024


def _new() -> "hashlib._Hash":
    return hashlib.blake2b(digest_size=20)


def partial_hash(path: str, size: Optional[int] = None) -> str:
    """Hash van begin + einde (+ grootte). Voor kleine bestanden is dit de volledige hash."""
    if size is None:
        size = os.path.getsize(path)
    h = _new()
    h.update(str(size).encode())
    with open(path, "rb") as fh:
        if size <= 2 * PARTIAL:
            h.update(fh.read())
        else:
            h.update(fh.read(PARTIAL))
            fh.seek(size - PARTIAL)
            h.update(fh.read(PARTIAL))
    return h.hexdigest()


def full_hash(path: str, control: Optional[JobControl] = None) -> str:
    h = _new()
    h.update(str(os.path.getsize(path)).encode())
    with open(path, "rb") as fh:
        while True:
            checkpoint(control)
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def files_identical(a: str, b: str, control: Optional[JobControl] = None) -> bool:
    """Byte-voor-byte controle."""
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                checkpoint(control)
                x, y = fa.read(CHUNK), fb.read(CHUNK)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False
