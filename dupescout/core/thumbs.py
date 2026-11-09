"""Thumbnail-cache op schijf."""

from __future__ import annotations

import hashlib
import os
from typing import Optional

from .db import app_home
from .imageio import is_image, load_preview


def thumb_dir() -> str:
    d = os.path.join(app_home(), "thumbs")
    os.makedirs(d, exist_ok=True)
    return d


def thumbnail_path(path: str, size: int = 256) -> Optional[str]:
    """Pad naar een JPEG-thumbnail; wordt gemaakt als hij nog niet bestaat. None bij fout."""
    ext = os.path.splitext(path)[1]
    if not is_image(ext):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(f"{os.path.realpath(path)}|{st.st_size}|{st.st_mtime_ns}|{size}".encode()).hexdigest()
    out = os.path.join(thumb_dir(), key[:2], key + ".jpg")
    if os.path.exists(out):
        return out
    try:
        img = load_preview(path, size)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        img.save(out, "JPEG", quality=82)
        return out
    except Exception:
        return None
