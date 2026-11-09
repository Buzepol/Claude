"""Datum van een bestand bepalen: EXIF/mediametadata -> bestandsnaam -> wijzigingsdatum."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Optional

from ..imageio import is_image, read_meta
from ..detectors.media import VIDEO_EXTS

_NAME_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")


def _parse(s: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if "T" in s or " " in s else s[:10], fmt)
        except ValueError:
            continue
    return None


def _video_date(path: str) -> Optional[datetime]:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time", "-of", "json", path],
            capture_output=True, timeout=30, check=True,
        ).stdout
        ct = json.loads(out).get("format", {}).get("tags", {}).get("creation_time")
        return _parse(ct.replace("Z", "")) if ct else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def get_date(path: str) -> tuple[datetime, str]:
    """(datum, bron) met bron in: exif, media, bestandsnaam, wijzigingsdatum."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if is_image(ext):
        try:
            taken = read_meta(path).taken
            d = _parse(taken) if taken else None
            if d:
                return d, "exif"
        except Exception:
            pass
    elif ext in VIDEO_EXTS:
        d = _video_date(path)
        if d:
            return d, "media"
    m = _NAME_DATE.search(os.path.basename(path))
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3])), "bestandsnaam"
        except ValueError:
            pass
    return datetime.fromtimestamp(os.stat(path).st_mtime), "wijzigingsdatum"
