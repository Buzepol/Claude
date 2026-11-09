"""Voorstel (dry-run) voor ordenen: raakt niets aan."""

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from ..db import Cache
from ..drives import VolumeInfo, volume_for_path
from ..hashing import files_identical
from ..jobs import JobControl, ProgressFn, checkpoint, report
from ..models import FileInfo
from .metadata import get_date
from .rules import Rule, first_match, render, template_context

_FORBIDDEN = re.compile(r'[<>:"\\|?*\x00-\x1f]')
MAX_NAME_BYTES = 255


def sanitize_component(part: str, vol: VolumeInfo) -> tuple[str, bool]:
    """Maak een pad-onderdeel veilig voor het doelsysteem. Geeft (naam, gewijzigd)."""
    orig = part
    part = unicodedata.normalize("NFC", part)
    part = part.replace("/", "-")
    if vol.restricted_names:
        part = _FORBIDDEN.sub("_", part).rstrip(". ")
    else:
        part = part.replace(":", "-").replace("\x00", "")
    while len(part.encode("utf-8")) > MAX_NAME_BYTES:
        stem, ext = os.path.splitext(part)
        part = stem[:-1] + ext
    return part or "_", part != orig


@dataclass
class PlanItem:
    src: str
    dst: str
    rule: str
    date: str
    date_source: str
    size: int
    status: str = "ok"  # ok | duplicate | already | skip
    note: str = ""
    include: bool = True

    @property
    def dest_dir(self) -> str:
        return os.path.dirname(self.dst)


@dataclass
class Plan:
    dest_root: str
    mode: str  # move | copy
    items: list[PlanItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def active(self) -> list[PlanItem]:
        return [i for i in self.items if i.include and i.status == "ok"]

    def summary(self) -> dict[str, int]:
        c = Counter(i.status for i in self.items)
        return {"total": len(self.items), "ok": c["ok"], "duplicate": c["duplicate"], "already": c["already"], "skip": c["skip"]}

    def per_folder(self) -> dict[str, tuple[int, int]]:
        out: dict[str, list[int]] = {}
        for i in self.active():
            rel = os.path.relpath(i.dest_dir, self.dest_root)
            e = out.setdefault(rel, [0, 0])
            e[0] += 1
            e[1] += i.size
        return {k: (v[0], v[1]) for k, v in sorted(out.items())}


def _resolve_conflict(src: str, dst: str, taken: set[str]) -> tuple[str, bool]:
    """Vrije bestandsnaam met ' (n)'. Geeft (pad, is_duplicaat): is_duplicaat als er al een identiek bestand staat."""
    base, ext = os.path.splitext(dst)
    n = 1
    cand = dst
    while True:
        if os.path.exists(cand):
            if files_identical(src, cand):
                return cand, True
        elif cand.casefold() not in taken:
            return cand, False
        n += 1
        cand = f"{base} ({n}){ext}"


def build_plan(
    files: list[FileInfo],
    rules: list[Rule],
    dest_root: str,
    mode: str = "move",
    conflict: str = "number",  # number | skip
    control: Optional[JobControl] = None,
    progress: Optional[ProgressFn] = None,
) -> Plan:
    if mode not in ("move", "copy"):
        raise ValueError("mode moet 'move' of 'copy' zijn")
    dest_root = os.path.abspath(dest_root)
    probe = dest_root
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    vol = volume_for_path(probe)
    plan = Plan(dest_root, mode)
    if not vol.writable:
        plan.warnings.append(f"Doelvolume '{vol.name}' ({vol.fs_type}) is alleen-lezen: uitvoeren is niet mogelijk.")
    taken: set[str] = set()
    changed_names = 0
    for n, f in enumerate(sorted(files, key=lambda x: x.path), 1):
        checkpoint(control)
        if n % 25 == 0:
            report(progress, "plan", n, len(files), f.path)
        try:
            when, source = get_date(f.path)
        except OSError as exc:
            plan.items.append(PlanItem(f.path, f.path, "", "", "", f.size, "skip", f"niet leesbaar: {exc.strerror}", False))
            continue
        src_vol = volume_for_path(f.path)
        rule = first_match(rules, f.path, f.size, when, f.root, src_vol.name)
        if rule is None:
            plan.items.append(PlanItem(f.path, f.path, "", when.strftime("%Y-%m-%d"), source, f.size, "skip", "geen regel past", False))
            continue
        ctx = template_context(f.path, when, f.root)
        rel = render(rule.dest, ctx).replace("\\", "/").strip("/")
        parts = []
        for p in rel.split("/"):
            if p in ("", ".", ".."):
                continue
            clean, changed = sanitize_component(p, vol)
            changed_names += changed
            parts.append(clean)
        name, ch = sanitize_component(ctx["bestandsnaam"], vol)
        changed_names += ch
        dst = os.path.join(dest_root, *parts, name)
        item = PlanItem(f.path, dst, rule.name, when.strftime("%Y-%m-%d"), source, f.size)
        if os.path.realpath(dst) == os.path.realpath(f.path):
            item.status, item.include, item.note = "already", False, "staat al op de juiste plek"
        elif dst.casefold() in taken or os.path.exists(dst):
            new_dst, is_dup = _resolve_conflict(f.path, dst, taken)
            if is_dup:
                item.status, item.include, item.note = "duplicate", False, "identiek bestand bestaat al op de doellocatie"
            elif conflict == "skip":
                item.status, item.include, item.note = "skip", False, "naamconflict (overgeslagen)"
            else:
                item.dst = new_dst
                item.note = "naamconflict: hernoemd"
        if item.status == "ok":
            taken.add(item.dst.casefold())
        plan.items.append(item)
    if changed_names:
        plan.warnings.append(f"{changed_names} map-/bestandsna(a)m(en) aangepast voor het doelsysteem ({vol.fs_type}).")
    report(progress, "plan", len(files), len(files), "")
    return plan
