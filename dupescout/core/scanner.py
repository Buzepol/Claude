"""Mappen doorlopen (read-only) en bestandsmetadata verzamelen."""

from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import dataclass, field
from typing import Optional

from .drives import volume_for_path
from .jobs import JobControl, ProgressFn, checkpoint, report
from .models import FileInfo, nfc

# Nooit doorzoeken: Apple Foto's-bibliotheken (geen instelling om dit uit te zetten).
FORBIDDEN_DIR_SUFFIXES = (".photoslibrary",)
QUARANTINE_DIR = ".dupescout-quarantine"

DEFAULT_EXCLUDED_DIRS = {
    ".Spotlight-V100",
    ".fseventsd",
    ".Trashes",
    ".TemporaryItems",
    ".DocumentRevisions-V100",
    ".VolumeIcon.icns",
    "$RECYCLE.BIN",
    "System Volume Information",
    QUARANTINE_DIR,
}
DEFAULT_EXCLUDED_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized", ".VolumeIcon.icns"}


@dataclass
class ScanOptions:
    excludes: list[str] = field(default_factory=list)  # glob-patronen op naam of pad
    min_size: int = 1
    max_size: Optional[int] = None
    extensions: Optional[set[str]] = None  # None = alles
    include_hidden: bool = True


@dataclass
class ScanResult:
    files: list[FileInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_libraries: list[str] = field(default_factory=list)


def is_forbidden_dir(name: str) -> bool:
    low = name.lower()
    return any(low.endswith(s) for s in FORBIDDEN_DIR_SUFFIXES)


def _excluded(name: str, path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(path, p) for p in patterns)


def scan_roots(
    roots: list[str],
    options: Optional[ScanOptions] = None,
    control: Optional[JobControl] = None,
    progress: Optional[ProgressFn] = None,
) -> ScanResult:
    options = options or ScanOptions()
    result = ScanResult()
    seen: set[str] = set()
    count = 0
    for root in roots:
        root = os.path.realpath(root)
        if not os.path.isdir(root):
            result.errors.append(f"{root}: geen map")
            continue
        vol = volume_for_path(root)
        root_dev = os.stat(root).st_dev
        stack = [root]
        while stack:
            checkpoint(control)
            current = stack.pop()
            try:
                it = os.scandir(current)
            except OSError as exc:
                result.errors.append(f"{current}: {exc.strerror or exc}")
                continue
            with it:
                for entry in it:
                    name = entry.name
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if is_forbidden_dir(name):
                                result.skipped_libraries.append(entry.path)
                                continue
                            if name in DEFAULT_EXCLUDED_DIRS or _excluded(name, entry.path, options.excludes):
                                continue
                            if not options.include_hidden and name.startswith("."):
                                continue
                            if entry.stat(follow_symlinks=False).st_dev != root_dev:
                                continue  # andere schijf/mount binnen de map niet volgen
                            stack.append(entry.path)
                            continue
                        if name in DEFAULT_EXCLUDED_FILES or name.startswith("._"):
                            continue
                        if not options.include_hidden and name.startswith("."):
                            continue
                        if _excluded(name, entry.path, options.excludes):
                            continue
                        st = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        result.errors.append(f"{entry.path}: {exc.strerror or exc}")
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    if st.st_size < options.min_size:
                        continue
                    if options.max_size is not None and st.st_size > options.max_size:
                        continue
                    if options.extensions is not None:
                        ext = os.path.splitext(name)[1].lower().lstrip(".")
                        if ext not in options.extensions:
                            continue
                    path = entry.path
                    if path in seen:
                        continue
                    seen.add(path)
                    result.files.append(
                        FileInfo(
                            path=path,
                            size=st.st_size,
                            mtime_ns=st.st_mtime_ns,
                            dev=st.st_dev,
                            inode=st.st_ino,
                            nlink=st.st_nlink,
                            volume_id=vol.volume_id,
                            relpath=nfc(os.path.relpath(path, vol.mount_point)),
                            root=root,
                        )
                    )
                    count += 1
                    if count % 500 == 0:
                        report(progress, "scan", count, 0, current)
    report(progress, "scan", count, count, "")
    return result
