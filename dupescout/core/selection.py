"""Slimme keuze welke kopie behouden wordt."""

from __future__ import annotations

import os
from typing import Callable, Iterable, Optional

from .models import DupGroup, FileInfo

STRATEGIES = {
    "oldest": "Behoud oudste (wijzigingsdatum)",
    "newest": "Behoud nieuwste",
    "shortest_path": "Behoud kortste pad",
    "largest": "Behoud grootste bestand",
    "highest_resolution": "Behoud hoogste resolutie (foto's)",
}


def _under(path: str, roots: Iterable[str]) -> bool:
    p = os.path.realpath(path)
    return any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in (os.path.realpath(x) for x in roots))


def pick_keeper(
    files: list[FileInfo],
    strategy: str = "oldest",
    prefer_roots: Iterable[str] = (),
    resolution: Optional[Callable[[FileInfo], int]] = None,
) -> FileInfo:
    """Kies het bestand dat blijft. Voorkeursmappen gaan voor; daarna de strategie; daarna pad (deterministisch)."""
    if not files:
        raise ValueError("lege groep")
    prefer_roots = list(prefer_roots)
    cands = files
    if prefer_roots:
        pref = [f for f in files if _under(f.path, prefer_roots)]
        if pref:
            cands = pref
    tie = lambda f: (len(f.path), f.path)  # noqa: E731
    if strategy == "oldest":
        key = lambda f: (f.mtime_ns, *tie(f))  # noqa: E731
    elif strategy == "newest":
        key = lambda f: (-f.mtime_ns, *tie(f))  # noqa: E731
    elif strategy == "shortest_path":
        key = tie
    elif strategy == "largest":
        key = lambda f: (-f.size, *tie(f))  # noqa: E731
    elif strategy == "highest_resolution":
        res = resolution or (lambda f: 0)
        key = lambda f: (-res(f), -f.size, f.mtime_ns, *tie(f))  # noqa: E731
    else:
        raise ValueError(f"onbekende strategie: {strategy}")
    return min(cands, key=key)


def auto_select(
    group: DupGroup,
    strategy: str = "oldest",
    prefer_roots: Iterable[str] = (),
    resolution: Optional[Callable[[FileInfo], int]] = None,
) -> tuple[FileInfo, list[FileInfo]]:
    """(keeper, te verwijderen) voor een groep. Hardlink-aliassen van de keeper blijven staan."""
    keeper = pick_keeper(group.files, strategy, prefer_roots, resolution)
    lid = keeper.link_id()
    rest = [f for f in group.files if f.path != keeper.path and not (lid and f.link_id() == lid)]
    return keeper, rest
