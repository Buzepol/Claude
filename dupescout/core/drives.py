"""Schijf-/volume-herkenning (macOS: diskutil; elders: /proc/mounts-fallback)."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

FAT_LIKE = {"exfat", "msdos", "fat32", "fat", "vfat", "ntfs", "ntfs-3g", "fuseblk"}


@dataclass(frozen=True)
class VolumeInfo:
    mount_point: str
    volume_id: str
    fs_type: str  # genormaliseerd, kleine letters: apfs, hfs, exfat, msdos, ntfs, ext4 ...
    writable: bool
    case_sensitive: bool
    name: str

    @property
    def restricted_names(self) -> bool:
        """Bestandssystemen waar tekens als : * ? " < > | niet mogen."""
        return self.fs_type in FAT_LIKE

    @property
    def coarse_timestamps(self) -> bool:
        return self.fs_type in {"exfat", "msdos", "fat32", "fat", "vfat"}


_cache: dict[str, VolumeInfo] = {}


def mount_point_of(path: str) -> str:
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def _diskutil(mount: str) -> Optional[dict]:
    try:
        out = subprocess.run(
            ["diskutil", "info", "-plist", mount], capture_output=True, timeout=15, check=True
        ).stdout
        return plistlib.loads(out)
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException, ValueError):
        return None


def _normalize_fs(raw: str) -> str:
    raw = (raw or "").lower()
    if raw.startswith("hfs") or raw == "jhfs+":
        return "hfs"
    if raw in {"fat32", "msdos"}:
        return "msdos"
    return raw


def _from_diskutil(mount: str, info: dict) -> VolumeInfo:
    fs = _normalize_fs(str(info.get("FilesystemType") or info.get("FilesystemName") or ""))
    uuid = info.get("VolumeUUID") or info.get("DiskUUID")
    st_dev = os.stat(mount).st_dev
    volume_id = str(uuid) if uuid else f"dev{st_dev}"
    writable = bool(info.get("WritableVolume", info.get("Writable", True)))
    if writable and not os.access(mount, os.W_OK):
        writable = False
    case = any("case-sensitive" in str(v).lower() for v in info.values())
    return VolumeInfo(mount, volume_id, fs, writable, case, str(info.get("VolumeName") or os.path.basename(mount) or mount))


def _proc_mounts(mount: str) -> tuple[str, str, bool]:
    """(device, fstype, read_only) via /proc/mounts."""
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 4 and parts[1].replace("\\040", " ") == mount:
                    return parts[0], parts[2], "ro" in parts[3].split(",")
    except OSError:
        pass
    return "", "", False


def _linux_uuid(device: str) -> Optional[str]:
    base = "/dev/disk/by-uuid"
    try:
        for name in os.listdir(base):
            if os.path.realpath(os.path.join(base, name)) == os.path.realpath(device):
                return name
    except OSError:
        pass
    return None


def volume_for_path(path: str) -> VolumeInfo:
    mount = mount_point_of(path)
    if mount in _cache:
        return _cache[mount]
    vol: Optional[VolumeInfo] = None
    if sys.platform == "darwin":
        info = _diskutil(mount)
        if info:
            vol = _from_diskutil(mount, info)
    if vol is None:
        device, fs, ro = _proc_mounts(mount)
        uuid = _linux_uuid(device) if device else None
        st_dev = os.stat(mount).st_dev
        vol = VolumeInfo(
            mount_point=mount,
            volume_id=uuid or f"dev{st_dev}",
            fs_type=_normalize_fs(fs) or "unknown",
            writable=(not ro) and os.access(mount, os.W_OK),
            case_sensitive=sys.platform != "darwin",
            name=os.path.basename(mount) or mount,
        )
    _cache[mount] = vol
    return vol


def list_external_volumes() -> list[str]:
    """Mountpoints onder /Volumes (macOS), voor de keuzelijst in de app."""
    base = "/Volumes"
    if not os.path.isdir(base):
        return []
    return sorted(os.path.join(base, n) for n in os.listdir(base) if os.path.isdir(os.path.join(base, n)))


def clear_cache() -> None:
    _cache.clear()
