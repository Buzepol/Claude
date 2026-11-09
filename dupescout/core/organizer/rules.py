"""Regel-engine voor ordenen (YAML)."""

from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import yaml

MAANDEN = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"]

PRESET_DATE = """\
# Standaard: alles op datum (jaar/maand)
- naam: Alles op datum
  doel: "{jaar}/{maand:02d}-{maandnaam}"
"""

PRESET_PHOTOS = """\
- naam: Foto's op datum
  als: { extensie: [jpg, jpeg, heic, heif, png, tif, tiff, cr2, cr3, nef, arw, dng, orf, rw2, raf] }
  doel: "Foto's/{jaar}/{maand:02d}-{maandnaam}"
- naam: Video's op datum
  als: { extensie: [mov, mp4, m4v, avi, mkv] }
  doel: "Video's/{jaar}/{maand:02d}-{maandnaam}"
- naam: Documenten per type
  als: { extensie: [pdf, doc, docx, xls, xlsx, ppt, pptx, txt, pages, numbers, key] }
  doel: "Documenten/{extensie}"
- naam: Overig
  doel: "Overig/{jaar}"
"""


class RuleError(ValueError):
    pass


def parse_size(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    m = re.fullmatch(r"\s*([\d.]+)\s*([kmgt]?i?b?)\s*", str(value), re.IGNORECASE)
    if not m:
        raise RuleError(f"ongeldige grootte: {value!r}")
    mult = {"": 1, "b": 1, "k": 1024, "kb": 1024, "m": 1024**2, "mb": 1024**2, "g": 1024**3, "gb": 1024**3, "t": 1024**4, "tb": 1024**4}
    return int(float(m[1]) * mult[m[2].lower().replace("ib", "b")])


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else [v]


@dataclass
class Rule:
    name: str
    dest: str
    when: dict[str, Any] = field(default_factory=dict)

    def matches(self, path: str, size: int, when: datetime, source_dir: str = "", volume_name: str = "") -> bool:
        w = self.when
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        name = os.path.basename(path)
        if "extensie" in w and ext not in {str(e).lower().lstrip(".") for e in _as_list(w["extensie"])}:
            return False
        if "mime" in w:
            mime = mimetypes.guess_type(path)[0] or ""
            if not any(mime.startswith(str(m)) for m in _as_list(w["mime"])):
                return False
        if "naam_regex" in w and not re.search(str(w["naam_regex"]), name):
            return False
        if "groter_dan" in w and size <= parse_size(w["groter_dan"]):
            return False
        if "kleiner_dan" in w and size >= parse_size(w["kleiner_dan"]):
            return False
        if "datum_voor" in w and when >= _date(w["datum_voor"]):
            return False
        if "datum_na" in w and when < _date(w["datum_na"]):
            return False
        if "bronmap" in w:
            sd = os.path.realpath(source_dir or os.path.dirname(path))
            if not any(sd == os.path.realpath(str(b)) or sd.startswith(os.path.realpath(str(b)) + os.sep) for b in _as_list(w["bronmap"])):
                return False
        if "schijf" in w and volume_name not in {str(s) for s in _as_list(w["schijf"])}:
            return False
        return True


def _date(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    if hasattr(v, "year") and hasattr(v, "month"):  # datetime.date
        return datetime(v.year, v.month, v.day)
    try:
        return datetime.strptime(str(v), "%Y-%m-%d")
    except ValueError as exc:
        raise RuleError(f"ongeldige datum: {v!r} (gebruik JJJJ-MM-DD)") from exc


def template_context(path: str, when: datetime, source_dir: str = "") -> dict[str, Any]:
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    return {
        "jaar": when.year,
        "maand": when.month,
        "maandnaam": MAANDEN[when.month - 1],
        "dag": when.day,
        "extensie": ext.lower().lstrip(".") or "zonder-extensie",
        "bestandsnaam": base,
        "naam": stem,
        "bron_map": os.path.basename(os.path.dirname(path)) or "root",
    }


def render(dest: str, ctx: dict[str, Any]) -> str:
    try:
        return dest.format(**ctx)
    except (KeyError, IndexError, ValueError) as exc:
        raise RuleError(f"ongeldig doelsjabloon {dest!r}: {exc}") from exc


def load_rules(text: str) -> list[Rule]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RuleError(f"YAML-fout: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise RuleError("regels moeten een lijst zijn met minstens één regel")
    rules = []
    for i, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise RuleError(f"regel {i} is geen object")
        dest = item.get("doel") or item.get("dest")
        if not dest:
            raise RuleError(f"regel {i}: 'doel' ontbreekt")
        when = item.get("als") or item.get("when") or {}
        unknown = set(when) - {"extensie", "mime", "naam_regex", "groter_dan", "kleiner_dan", "datum_voor", "datum_na", "bronmap", "schijf"}
        if unknown:
            raise RuleError(f"regel {i}: onbekende voorwaarde(n): {', '.join(sorted(unknown))}")
        rule = Rule(str(item.get("naam") or item.get("name") or f"Regel {i}"), str(dest), when)
        render(rule.dest, template_context("voorbeeld.jpg", datetime(2000, 1, 1)))  # valideer sjabloon
        if "naam_regex" in when:
            try:
                re.compile(str(when["naam_regex"]))
            except re.error as exc:
                raise RuleError(f"regel {i}: ongeldige regex: {exc}") from exc
        rules.append(rule)
    return rules


def first_match(rules: list[Rule], path: str, size: int, when: datetime, source_dir: str = "", volume_name: str = "") -> Optional[Rule]:
    for r in rules:
        if r.matches(path, size, when, source_dir, volume_name):
            return r
    return None
