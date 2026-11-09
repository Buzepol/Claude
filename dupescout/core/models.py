"""Gedeelde datamodellen."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    mtime_ns: int
    dev: int
    inode: int
    nlink: int
    volume_id: str
    relpath: str  # relatief aan het mountpoint van het volume
    root: str  # scanmap waaronder het bestand gevonden is

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].lower().lstrip(".")

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]

    def link_id(self) -> Optional[tuple[int, int]]:
        """(dev, inode) als dit bestand een hardlink kan zijn, anders None."""
        if self.nlink > 1 and self.inode:
            return (self.dev, self.inode)
        return None


@dataclass
class DupGroup:
    kind: str  # exact | similar_image | name_size | similar_media
    key: str
    files: list[FileInfo]
    confidence: str  # certain | probable | possible
    note: str = ""

    @property
    def reclaimable(self) -> int:
        """Ruimte die vrijkomt als slechts één (de grootste) kopie blijft staan."""
        if len(self.files) < 2:
            return 0
        sizes = [f.size for f in self.files]
        return sum(sizes) - max(sizes)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
