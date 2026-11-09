"""Mogelijke duplicaten op genormaliseerde naam + grootte (snel, onzeker)."""

from __future__ import annotations

import re
from collections import defaultdict

from ..models import DupGroup, FileInfo, nfc

_COPY_SUFFIX = re.compile(r"(\s*\(\d+\)|[\s_-]+(copy|kopie)(\s*\d+)?|\s+-\s+\w*(copy|kopie))\s*$", re.IGNORECASE)


def normalize_name(stem: str) -> str:
    s = nfc(stem).casefold().strip()
    prev = None
    while prev != s:
        prev = s
        s = _COPY_SUFFIX.sub("", s).strip()
    return s


def find_name_size(files: list[FileInfo]) -> list[DupGroup]:
    buckets: dict[tuple[str, str, int], list[FileInfo]] = defaultdict(list)
    for f in files:
        if f.size > 0:
            buckets[(normalize_name(f.stem), f.ext, f.size)].append(f)
    out = []
    for (name, ext, size), members in buckets.items():
        if len(members) > 1:
            out.append(DupGroup("name_size", f"{name}.{ext}:{size}", sorted(members, key=lambda x: x.path), "possible"))
    out.sort(key=lambda g: g.reclaimable, reverse=True)
    return out
