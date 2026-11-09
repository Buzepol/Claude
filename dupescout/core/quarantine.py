"""Quarantaine: veilig verplaatsen op hetzelfde volume, herstellen en handmatig wissen.

Veiligheidsregels (in de kern afgedwongen, niet alleen in de UI):
* per te verplaatsen bestand moet minstens één behouden kopie bestaan die zelf niet verplaatst wordt;
* bij exacte duplicaten wordt byte-voor-byte gecontroleerd dat de kopie identiek is;
* alleen schrijfbare volumes; niets wordt automatisch definitief verwijderd.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from dataclasses import dataclass, field
from typing import Optional

from .db import Cache
from .drives import volume_for_path
from .hashing import files_identical
from .scanner import QUARANTINE_DIR

SIDECAR_EXTS = {"xmp", "aae"}
LIVE_EXTS = {"mov"}
LIVE_STILL_EXTS = {"heic", "heif", "jpg", "jpeg"}


class QuarantineError(Exception):
    pass


@dataclass
class QuarantineRequest:
    path: str
    keepers: list[str]
    reason: str = "duplicaat"
    verify_identical: bool = True  # False voor "gelijkende" (niet-identieke) foto's


@dataclass
class BatchResult:
    batch_ids: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    bytes_moved: int = 0


def quarantine_base(path: str) -> str:
    """Map waarin '.dupescout-quarantine' komt: het mountpoint van het volume van het bestand.

    Is dat mountpoint niet beschrijfbaar (bv. de alleen-lezen systeemschijf '/' op macOS, terwijl de
    thuismap op het Data-volume staat), dan wordt de thuismap gebruikt als die op dezelfde schijf staat.
    """
    vol = volume_for_path(path)
    if vol.writable:
        return vol.mount_point
    home = os.path.expanduser("~")
    try:
        if os.path.exists(home) and os.stat(home).st_dev == os.stat(path).st_dev and os.access(home, os.W_OK):
            return home
    except OSError:
        pass
    return vol.mount_point


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def companions(path: str, keepers: list[str]) -> list[str]:
    """Bijbehorende bestanden die mee moeten: sidecars altijd; Live Photo-video alleen als een keeper er ook een heeft."""
    base, ext = os.path.splitext(path)
    found = []
    d = os.path.dirname(path) or "."
    try:
        names = os.listdir(d)
    except OSError:
        return []
    stem = os.path.basename(base)
    for n in names:
        nb, ne = os.path.splitext(n)
        ne = ne.lower().lstrip(".")
        full = os.path.join(d, n)
        if not os.path.isfile(full) or os.path.islink(full):
            continue
        if nb == stem and ne in SIDECAR_EXTS:
            found.append(full)
        elif nb == stem and ne in LIVE_EXTS and ext.lower().lstrip(".") in LIVE_STILL_EXTS:
            for k in keepers:
                kb = os.path.splitext(k)[0]
                if any(os.path.exists(kb + e) for e in (".mov", ".MOV", ".Mov")):
                    found.append(full)
                    break
    return found


def _manifest_path(batch_dir: str) -> str:
    return os.path.join(batch_dir, "manifest.json")


def _write_manifest(batch_dir: str, data: dict) -> None:
    tmp = _manifest_path(batch_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, _manifest_path(batch_dir))


def new_batch_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def move_to_quarantine(
    requests: list[QuarantineRequest],
    cache: Optional[Cache] = None,
    batch_id: Optional[str] = None,
) -> BatchResult:
    result = BatchResult()
    batch_id = batch_id or new_batch_id()
    delete_set = {os.path.realpath(r.path) for r in requests}
    by_mount: dict[str, list[QuarantineRequest]] = {}
    for r in requests:
        by_mount.setdefault(quarantine_base(r.path), []).append(r)

    for mount, reqs in by_mount.items():
        vol = volume_for_path(mount)
        if not (vol.writable and os.access(mount, os.W_OK)):
            for r in reqs:
                result.failed.append((r.path, f"volume '{vol.name}' ({vol.fs_type}) is alleen-lezen"))
            continue
        batch_dir = os.path.join(mount, QUARANTINE_DIR, batch_id)
        os.makedirs(os.path.join(batch_dir, "files"), exist_ok=True)
        manifest = {
            "batch_id": batch_id,
            "created": time.time(),
            "volume_id": vol.volume_id,
            "mount_point": mount,
            "items": [],
        }
        counter = 0
        for r in reqs:
            try:
                keepers = [k for k in r.keepers if os.path.realpath(k) not in delete_set and os.path.exists(k)]
                keepers = [k for k in keepers if not _same_file(k, r.path)]
                if not keepers:
                    raise QuarantineError("geen behouden kopie gevonden (er blijft altijd minstens één staan)")
                if not os.path.isfile(r.path) or os.path.islink(r.path):
                    raise QuarantineError("bestand bestaat niet meer")
                if r.verify_identical and not any(files_identical(r.path, k) for k in keepers):
                    raise QuarantineError("inhoud wijkt af van de behouden kopie")
                paths = [r.path] + [c for c in companions(r.path, keepers) if os.path.realpath(c) not in delete_set]
                for p in paths:
                    rel = os.path.relpath(p, mount)
                    dest = os.path.join(batch_dir, "files", rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    st = os.stat(p)
                    os.rename(p, dest)  # zelfde volume: direct en omkeerbaar
                    counter += 1
                    manifest["items"].append({
                        "id": counter,
                        "original": p,
                        "stored": dest,
                        "size": st.st_size,
                        "mtime_ns": st.st_mtime_ns,
                        "reason": r.reason if p == r.path else "bijbehorend bestand",
                        "keepers": keepers,
                        "state": "stored",
                    })
                    result.moved.append(p)
                    result.bytes_moved += st.st_size
            except (OSError, QuarantineError) as exc:
                result.failed.append((r.path, str(exc)))
        _write_manifest(batch_dir, manifest)
        if manifest["items"]:
            result.batch_ids.append(batch_id)
            if cache:
                cache.register_quarantine_mount(mount)
        else:
            shutil.rmtree(batch_dir, ignore_errors=True)
    if cache and result.moved:
        cache.log("quarantine", {"batch": batch_id, "files": len(result.moved), "bytes": result.bytes_moved})
    return result


# ---- beheer -----------------------------------------------------------------

@dataclass
class Batch:
    batch_id: str
    mount_point: str
    volume_id: str
    created: float
    dir: str
    items: list[dict]

    @property
    def stored_items(self) -> list[dict]:
        return [i for i in self.items if i["state"] == "stored"]

    @property
    def stored_count(self) -> int:
        return len(self.stored_items)

    @property
    def stored_bytes(self) -> int:
        return sum(i["size"] for i in self.stored_items)


def load_batch(batch_dir: str) -> Optional[Batch]:
    try:
        with open(_manifest_path(batch_dir), encoding="utf-8") as fh:
            m = json.load(fh)
        return Batch(m["batch_id"], m["mount_point"], m["volume_id"], m["created"], batch_dir, m["items"])
    except (OSError, ValueError, KeyError):
        return None


def list_batches(cache: Optional[Cache] = None, extra_mounts: tuple[str, ...] = ()) -> list[Batch]:
    mounts = set(extra_mounts)
    if cache:
        mounts.update(cache.quarantine_mounts())
    out = []
    for mount in sorted(mounts):
        qdir = os.path.join(mount, QUARANTINE_DIR)
        if not os.path.isdir(qdir):
            continue
        for name in sorted(os.listdir(qdir)):
            b = load_batch(os.path.join(qdir, name))
            if b:
                out.append(b)
    out.sort(key=lambda b: b.created, reverse=True)
    return out


def _free_name(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 1
    while True:
        cand = f"{base} (hersteld{'' if n == 1 else ' ' + str(n)}){ext}"
        if not os.path.exists(cand):
            return cand
        n += 1


def restore(batch: Batch, item_ids: Optional[list[int]] = None, cache: Optional[Cache] = None) -> list[str]:
    """Zet bestanden terug op de oorspronkelijke plek (bij naamconflict: '(hersteld)')."""
    restored = []
    for item in batch.stored_items:
        if item_ids is not None and item["id"] not in item_ids:
            continue
        if not os.path.exists(item["stored"]):
            item["state"] = "missing"
            continue
        dest = _free_name(item["original"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.rename(item["stored"], dest)
        item["state"] = "restored"
        restored.append(dest)
    _write_manifest(batch.dir, _as_manifest(batch))
    _cleanup_empty(batch)
    if cache and restored:
        cache.log("quarantine_restore", {"batch": batch.batch_id, "files": len(restored)})
    return restored


def purge(batch: Batch, confirmed: bool = False, item_ids: Optional[list[int]] = None, cache: Optional[Cache] = None) -> tuple[int, int]:
    """Definitief wissen. Vereist expliciet confirmed=True; wordt nooit automatisch aangeroepen.

    Geeft (aantal bestanden, bytes) terug.
    """
    if confirmed is not True:
        raise QuarantineError("definitief wissen vereist expliciete bevestiging")
    count = size = 0
    for item in batch.stored_items:
        if item_ids is not None and item["id"] not in item_ids:
            continue
        try:
            os.remove(item["stored"])
        except FileNotFoundError:
            pass
        item["state"] = "purged"
        count += 1
        size += item["size"]
    _write_manifest(batch.dir, _as_manifest(batch))
    _cleanup_empty(batch)
    if cache and count:
        cache.log("quarantine_purge", {"batch": batch.batch_id, "files": count, "bytes": size})
    return count, size


def _as_manifest(batch: Batch) -> dict:
    return {
        "batch_id": batch.batch_id,
        "created": batch.created,
        "volume_id": batch.volume_id,
        "mount_point": batch.mount_point,
        "items": batch.items,
    }


def _cleanup_empty(batch: Batch) -> None:
    """Verwijder lege mappen; verwijder de batch zelf als er niets meer in zit."""
    files_dir = os.path.join(batch.dir, "files")
    if os.path.isdir(files_dir):
        for dirpath, _dirs, _files in os.walk(files_dir, topdown=False):
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
    if not batch.stored_items and not os.path.isdir(files_dir):
        shutil.rmtree(batch.dir, ignore_errors=True)
        qdir = os.path.dirname(batch.dir)
        try:
            os.rmdir(qdir)
        except OSError:
            pass
