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


def resolution_lookup(cache) -> Callable[[FileInfo], int]:
    """Functie die het aantal pixels van een foto geeft (uit cache, anders uit het bestand)."""
    from .imageio import is_image, read_meta

    def res(f: FileInfo) -> int:
        got = cache.lookup(f)
        if "width" in got and "height" in got:
            return got["width"] * got["height"]
        if is_image(f.ext):
            try:
                m = read_meta(f.path)
                return m.width * m.height
            except Exception:
                return 0
        return 0

    return res


def auto_select_all(
    groups: list[DupGroup],
    strategy: str = "oldest",
    prefer_roots: Iterable[str] = (),
    resolution: Optional[Callable[[FileInfo], int]] = None,
    skip_kinds: tuple[str, ...] = ("name_size",),
) -> set[str]:
    """Markeer in alle groepen de te verwijderen bestanden (paden).

    Groepen kunnen overlappen (een exacte kopie zit ook in een 'gelijkende foto'-groep). Daarom: exacte
    groepen eerst; een bestand dat al als keeper is gekozen, wordt nooit gemarkeerd; een keeper wordt bij
    voorkeur gekozen uit bestanden die nog niet gemarkeerd zijn. ('name_size' is onzeker en wordt overgeslagen.)
    """
    order = {"exact": 0, "similar_image": 1, "similar_media": 2}
    marks: set[str] = set()
    keepers: set[str] = set()
    for g in sorted((g for g in groups if g.kind not in skip_kinds), key=lambda g: order.get(g.kind, 9)):
        already = [f for f in g.files if f.path in keepers]
        if already:
            keeper = already[0]
        else:
            pool = [f for f in g.files if f.path not in marks] or g.files
            keeper = pick_keeper(pool, strategy, prefer_roots, resolution)
        keepers.add(keeper.path)
        marks.discard(keeper.path)
        lid = keeper.link_id()
        for f in g.files:
            if f.path == keeper.path or f.path in keepers or (lid and f.link_id() == lid):
                continue
            marks.add(f.path)
    return marks


def build_requests(groups: list[DupGroup], marks: set[str]):
    """QuarantineRequests uit groepen + gemarkeerde paden. Geeft (requests, overgeslagen groepen).

    Een groep zonder overblijvende kopie wordt overgeslagen (er blijft altijd minstens één staan).
    """
    from .quarantine import QuarantineRequest

    reqs: dict[str, QuarantineRequest] = {}
    skipped: list[DupGroup] = []
    for g in groups:
        dele = [f for f in g.files if f.path in marks]
        keep = [f for f in g.files if f.path not in marks]
        if not dele:
            continue
        if not keep:
            skipped.append(g)
            continue
        for f in dele:
            r = reqs.get(f.path)
            if r is None:
                reqs[f.path] = QuarantineRequest(f.path, [k.path for k in keep], g.kind, verify_identical=(g.kind == "exact"))
            else:
                r.keepers += [k.path for k in keep]
                r.verify_identical = r.verify_identical and g.kind == "exact"
    return list(reqs.values()), skipped
