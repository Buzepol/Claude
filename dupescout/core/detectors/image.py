"""Gelijkende foto's via perceptuele hash."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ..db import Cache
from ..imageio import compute_phash, is_image
from ..jobs import JobControl, ProgressFn, checkpoint, report
from ..models import DupGroup, FileInfo
from ..perceptual import cluster_hashes


def image_hashes(
    files: list[FileInfo],
    cache: Cache,
    errors: list[str],
    control: Optional[JobControl] = None,
    progress: Optional[ProgressFn] = None,
    workers: Optional[int] = None,
) -> dict[str, int]:
    """path -> 64-bit pHash (uit cache of berekend)."""
    imgs = sorted((f for f in files if is_image(f.ext)), key=lambda f: f.path)
    result: dict[str, int] = {}
    lock = threading.Lock()
    done = [0]
    total = len(imgs)

    def one(f: FileInfo) -> None:
        checkpoint(control)
        got = cache.lookup(f)
        if "phash" in got:
            h = got["phash"]
        else:
            try:
                h, meta = compute_phash(f.path)
            except Exception as exc:  # corrupt/onbekend formaat
                with lock:
                    errors.append(f"{f.path}: {exc}")
                    done[0] += 1
                return
            cache.store(f, phash=h, width=meta.width, height=meta.height, taken=meta.taken)
        with lock:
            result[f.path] = h
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == total:
                report(progress, "images", done[0], total, f.path)

    workers = workers or min(8, (os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, imgs))
    cache.flush()
    return result


def find_similar_images(
    files: list[FileInfo],
    cache: Cache,
    threshold: int = 5,
    errors: Optional[list[str]] = None,
    control: Optional[JobControl] = None,
    progress: Optional[ProgressFn] = None,
    workers: Optional[int] = None,
) -> list[DupGroup]:
    errors = errors if errors is not None else []
    hashes = image_hashes(files, cache, errors, control, progress, workers)
    by_path = {f.path: f for f in files}
    clusters = cluster_hashes(hashes, threshold)
    out = []
    for members in clusters:
        fs = sorted((by_path[p] for p in members), key=lambda f: f.path)
        distinct = {hashes[f.path] for f in fs}
        conf = "probable" if len(distinct) == 1 else "possible"
        out.append(DupGroup("similar_image", f"{min(distinct):016x}", fs, conf))
    out.sort(key=lambda g: g.reclaimable, reverse=True)
    return out
