"""Exacte duplicaten: grootte -> gedeeltelijke hash -> volledige hash."""

from __future__ import annotations

import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from ..db import Cache
from ..hashing import PARTIAL, full_hash, partial_hash
from ..jobs import JobControl, ProgressFn, checkpoint, report
from ..models import DupGroup, FileInfo


def collapse_hardlinks(files: list[FileInfo]) -> list[FileInfo]:
    """Hardlinks naar dezelfde data tellen niet als duplicaat: houd er één per link-id."""
    seen: set[tuple[int, int]] = set()
    out = []
    for f in files:
        lid = f.link_id()
        if lid is not None:
            if lid in seen:
                continue
            seen.add(lid)
        out.append(f)
    return out


def hash_files(
    files: list[FileInfo],
    stage: str,
    compute: Callable[[FileInfo], Optional[str]],
    errors: list[str],
    control: Optional[JobControl],
    progress: Optional[ProgressFn],
) -> dict[str, str]:
    """Hash alle bestanden; per volume sequentieel (hdd-vriendelijk), volumes parallel."""
    by_volume: dict[str, list[FileInfo]] = defaultdict(list)
    for f in files:
        by_volume[f.volume_id].append(f)
    results: dict[str, str] = {}
    lock = threading.Lock()
    done = [0]
    total = len(files)

    def work(vol_files: list[FileInfo]) -> None:
        for f in sorted(vol_files, key=lambda x: x.path):
            checkpoint(control)
            try:
                h = compute(f)
            except OSError as exc:
                with lock:
                    errors.append(f"{f.path}: {exc.strerror or exc}")
                h = None
            with lock:
                if h is not None:
                    results[f.path] = h
                done[0] += 1
                if done[0] % 25 == 0 or done[0] == total:
                    report(progress, stage, done[0], total, f.path)

    if not by_volume:
        return results
    with ThreadPoolExecutor(max_workers=min(8, len(by_volume))) as pool:
        futures = [pool.submit(work, v) for v in by_volume.values()]
        for fut in futures:
            fut.result()
    return results


def _partial_for(cache: Cache) -> Callable[[FileInfo], Optional[str]]:
    def compute(f: FileInfo) -> Optional[str]:
        got = cache.lookup(f)
        if "partial" in got:
            return got["partial"]
        h = partial_hash(f.path, f.size)
        fields = {"partial": h}
        if f.size <= 2 * PARTIAL:
            fields["full"] = h  # kleine bestanden: gedeeltelijk == volledig
        cache.store(f, **fields)
        return h

    return compute


def _full_for(cache: Cache, control: Optional[JobControl]) -> Callable[[FileInfo], Optional[str]]:
    def compute(f: FileInfo) -> Optional[str]:
        got = cache.lookup(f)
        if "full" in got:
            return got["full"]
        h = full_hash(f.path, control)
        cache.store(f, full=h)
        return h

    return compute


def content_hashes(
    files: list[FileInfo],
    cache: Cache,
    errors: list[str],
    control: Optional[JobControl] = None,
    progress: Optional[ProgressFn] = None,
) -> dict[str, str]:
    """Volledige hash voor bestanden waarvan de grootte niet uniek is. (path -> hash)"""
    by_size: dict[int, list[FileInfo]] = defaultdict(list)
    for f in files:
        by_size[f.size].append(f)
    cands = [f for group in by_size.values() if len(group) > 1 for f in group]

    part = hash_files(cands, "partial", _partial_for(cache), errors, control, progress)
    by_part: dict[tuple[int, str], list[FileInfo]] = defaultdict(list)
    for f in cands:
        if f.path in part:
            by_part[(f.size, part[f.path])].append(f)
    cands2 = [f for g in by_part.values() if len(g) > 1 for f in g]

    full = hash_files(cands2, "full", _full_for(cache, control), errors, control, progress)
    cache.flush()
    return full


def find_exact(
    files: list[FileInfo],
    cache: Cache,
    errors: Optional[list[str]] = None,
    control: Optional[JobControl] = None,
    progress: Optional[ProgressFn] = None,
) -> list[DupGroup]:
    errors = errors if errors is not None else []
    files = [f for f in files if f.size > 0]
    hashes = content_hashes(files, cache, errors, control, progress)
    groups: dict[str, list[FileInfo]] = defaultdict(list)
    for f in files:
        h = hashes.get(f.path)
        if h:
            groups[h].append(f)
    out = []
    for h, members in groups.items():
        members = collapse_hardlinks(sorted(members, key=lambda x: x.path))
        if len(members) > 1:
            out.append(DupGroup("exact", h, members, "certain"))
    out.sort(key=lambda g: g.reclaimable, reverse=True)
    return out
