"""Hoofdingang voor CLI en UI: scannen + gekozen detectoren draaien."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .db import Cache
from .detectors.exact import find_exact
from .detectors.image import find_similar_images
from .detectors.media import find_similar_media
from .detectors.namesize import find_name_size
from .jobs import JobControl, ProgressFn, report
from .models import DupGroup, FileInfo
from .scanner import ScanOptions, scan_roots


@dataclass
class DetectOptions:
    exact: bool = True
    similar_images: bool = False
    name_size: bool = False
    similar_media: bool = False
    image_threshold: int = 5
    scan: ScanOptions = field(default_factory=ScanOptions)
    workers: Optional[int] = None


@dataclass
class DetectResult:
    files: list[FileInfo] = field(default_factory=list)
    groups: list[DupGroup] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_libraries: list[str] = field(default_factory=list)

    @property
    def reclaimable_exact(self) -> int:
        return sum(g.reclaimable for g in self.groups if g.kind == "exact")


def run_detection(
    roots: list[str],
    opts: DetectOptions,
    cache: Cache,
    control: Optional[JobControl] = None,
    progress: Optional[ProgressFn] = None,
) -> DetectResult:
    sc = scan_roots(roots, opts.scan, control, progress)
    res = DetectResult(files=sc.files, errors=list(sc.errors), skipped_libraries=sc.skipped_libraries)
    if opts.exact:
        res.groups += find_exact(sc.files, cache, res.errors, control, progress)
    if opts.similar_images:
        res.groups += find_similar_images(sc.files, cache, opts.image_threshold, res.errors, control, progress, opts.workers)
    if opts.similar_media:
        res.groups += find_similar_media(sc.files, cache, errors=res.errors, control=control, progress=progress)
    if opts.name_size:
        res.groups += find_name_size(sc.files)
    cache.log("scan", {"roots": roots, "files": len(sc.files), "groups": len(res.groups)})
    report(progress, "done", 1, 1, "")
    return res
