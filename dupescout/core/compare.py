"""Twee mappen vergelijken: A is de referentie, B wordt gecontroleerd."""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .db import Cache
from .detectors.exact import content_hashes
from .jobs import JobControl, ProgressFn
from .models import FileInfo
from .scanner import ScanOptions, scan_roots


@dataclass
class Match:
    """Dezelfde inhoud in A en B (en eventueel meerdere keren per kant)."""
    key: str
    a_files: list[FileInfo]
    b_files: list[FileInfo]

    @property
    def reclaimable(self) -> int:
        return sum(f.size for f in self.b_files)


@dataclass
class CompareResult:
    in_both: list[Match] = field(default_factory=list)
    only_a: list[FileInfo] = field(default_factory=list)
    only_b: list[FileInfo] = field(default_factory=list)
    linked: list[tuple[FileInfo, FileInfo]] = field(default_factory=list)  # hardlinks tussen A en B
    changed_paths: list[tuple[FileInfo, FileInfo]] = field(default_factory=list)  # zelfde relatief pad, andere inhoud
    errors: list[str] = field(default_factory=list)

    @property
    def reclaimable(self) -> int:
        return sum(m.reclaimable for m in self.in_both)


def _check_roots(a: str, b: str) -> tuple[str, str]:
    ra, rb = os.path.realpath(a), os.path.realpath(b)
    if ra == rb:
        raise ValueError("Map A en B zijn dezelfde map.")
    if rb.startswith(ra + os.sep) or ra.startswith(rb + os.sep):
        raise ValueError("Map A en B mogen niet in elkaar liggen.")
    return ra, rb


def compare_folders(
    folder_a: str,
    folder_b: str,
    cache: Cache,
    options: Optional[ScanOptions] = None,
    control: Optional[JobControl] = None,
    progress: Optional[ProgressFn] = None,
) -> CompareResult:
    ra, rb = _check_roots(folder_a, folder_b)
    res = CompareResult()
    sa = scan_roots([ra], options, control, progress)
    sb = scan_roots([rb], options, control, progress)
    res.errors += sa.errors + sb.errors
    a_files = [f for f in sa.files if f.size > 0]
    b_files = [f for f in sb.files if f.size > 0]
    # lege bestanden: gelijk als beide leeg zijn met dezelfde naam is niet interessant; negeren

    # hardlinks tussen A en B zijn geen duplicaten
    a_links = {f.link_id(): f for f in a_files if f.link_id()}
    linked_b: set[str] = set()
    for fb in b_files:
        lid = fb.link_id()
        if lid and lid in a_links:
            res.linked.append((a_links[lid], fb))
            linked_b.add(fb.path)
    linked_a = {fa.path for fa, _ in res.linked}
    a_files = [f for f in a_files if f.path not in linked_a]
    b_files = [f for f in b_files if f.path not in linked_b]

    # bestanden waarvan de grootte maar aan één kant voorkomt hoeven niet gehasht te worden
    sizes_a = {f.size for f in a_files}
    sizes_b = {f.size for f in b_files}
    shared = sizes_a & sizes_b
    cand = [f for f in a_files + b_files if f.size in shared]
    # content_hashes hasht alleen groottes die >1 keer voorkomen; dat is hier gegarandeerd
    hashes = content_hashes(cand, cache, res.errors, control, progress)

    ga: dict[str, list[FileInfo]] = defaultdict(list)
    gb: dict[str, list[FileInfo]] = defaultdict(list)
    for f in a_files:
        h = hashes.get(f.path)
        if h:
            ga[h].append(f)
    for f in b_files:
        h = hashes.get(f.path)
        if h:
            gb[h].append(f)

    matched_a: set[str] = set()
    matched_b: set[str] = set()
    for h, fa in ga.items():
        fb = gb.get(h)
        if fb:
            res.in_both.append(Match(h, sorted(fa, key=lambda x: x.path), sorted(fb, key=lambda x: x.path)))
            matched_a.update(f.path for f in fa)
            matched_b.update(f.path for f in fb)
    res.in_both.sort(key=lambda m: m.reclaimable, reverse=True)
    res.only_a = sorted((f for f in a_files if f.path not in matched_a), key=lambda f: f.path)
    res.only_b = sorted((f for f in b_files if f.path not in matched_b), key=lambda f: f.path)

    # zelfde relatief pad, andere inhoud (informatief)
    rel_b = {os.path.relpath(f.path, rb): f for f in res.only_b}
    for fa in res.only_a:
        fb = rel_b.get(os.path.relpath(fa.path, ra))
        if fb is not None:
            res.changed_paths.append((fa, fb))
    return res
