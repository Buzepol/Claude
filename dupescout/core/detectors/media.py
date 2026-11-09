"""Gelijkende video's (frame-hashes via ffmpeg) en audio (Chromaprint via fpcalc)."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional

import numpy as np

from ..db import Cache
from ..jobs import JobControl, ProgressFn, checkpoint, report
from ..models import DupGroup, FileInfo
from ..perceptual import hamming, phash_from_gray32

VIDEO_EXTS = {"mp4", "mov", "m4v", "avi", "mkv", "wmv", "mpg", "mpeg", "mts", "m2ts", "webm", "3gp"}
AUDIO_EXTS = {"mp3", "m4a", "aac", "flac", "wav", "aiff", "aif", "ogg", "opus", "wma"}
FRAME_POSITIONS = (0.1, 0.3, 0.5, 0.7, 0.9)


def tools_available() -> dict[str, bool]:
    return {"ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe")), "fpcalc": bool(shutil.which("fpcalc"))}


def probe_duration(path: str) -> Optional[float]:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, timeout=60, check=True,
        ).stdout
        return float(json.loads(out)["format"]["duration"])
    except (OSError, subprocess.SubprocessError, KeyError, ValueError):
        return None


def _frame_hash(path: str, t: float) -> Optional[int]:
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
             "-vf", "scale=32:32:flags=area,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=120, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if len(raw) < 1024:
        return None
    return phash_from_gray32(np.frombuffer(raw[:1024], dtype=np.uint8))


def video_signature(path: str, duration: float) -> Optional[list[int]]:
    sig = []
    for pos in FRAME_POSITIONS:
        h = _frame_hash(path, duration * pos)
        if h is None:
            return None
        sig.append(h)
    return sig


def audio_fingerprint(path: str, length: int = 120) -> Optional[list[int]]:
    if not shutil.which("fpcalc"):
        return None
    try:
        out = subprocess.run(
            ["fpcalc", "-raw", "-json", "-length", str(length), path],
            capture_output=True, timeout=120, check=True,
        ).stdout
        fp = json.loads(out)["fingerprint"]
        return [int(x) & 0xFFFFFFFF for x in fp]
    except (OSError, subprocess.SubprocessError, KeyError, ValueError):
        return None


def video_distance(a: list[int], b: list[int]) -> float:
    return sum(hamming(x, y) for x, y in zip(a, b)) / max(1, min(len(a), len(b)))


def audio_bit_error_rate(a: list[int], b: list[int]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 1.0
    return sum((x ^ y).bit_count() for x, y in zip(a[:n], b[:n])) / (32.0 * n)


def find_similar_media(
    files: list[FileInfo],
    cache: Cache,
    video_threshold: float = 8.0,
    audio_max_ber: float = 0.3,
    duration_tolerance: float = 2.0,
    errors: Optional[list[str]] = None,
    control: Optional[JobControl] = None,
    progress: Optional[ProgressFn] = None,
) -> list[DupGroup]:
    errors = errors if errors is not None else []
    media = [f for f in files if f.ext in VIDEO_EXTS or f.ext in AUDIO_EXTS]
    if not media:
        return []
    tools = tools_available()
    entries: list[tuple[FileInfo, float, str, list[int]]] = []
    for i, f in enumerate(sorted(media, key=lambda x: x.path)):
        checkpoint(control)
        report(progress, "media", i, len(media), f.path)
        is_video = f.ext in VIDEO_EXTS
        if is_video and not (tools["ffmpeg"] and tools["ffprobe"]):
            continue
        if not is_video and not tools["fpcalc"]:
            continue
        got = cache.lookup(f)
        sig = got.get("vsig")
        if sig and isinstance(sig, dict) and sig.get("kind") == ("v" if is_video else "a"):
            dur, data = sig["duration"], sig["data"]
        else:
            dur = probe_duration(f.path)
            if dur is None or dur <= 0:
                errors.append(f"{f.path}: duur onbekend")
                continue
            data = video_signature(f.path, dur) if is_video else audio_fingerprint(f.path)
            if data is None:
                errors.append(f"{f.path}: handtekening mislukt")
                continue
            cache.store(f, vsig={"kind": "v" if is_video else "a", "duration": dur, "data": data})
        entries.append((f, dur, "v" if is_video else "a", data))
    cache.flush()
    report(progress, "media", len(media), len(media), "")

    entries.sort(key=lambda e: (e[2], e[1]))
    parent = list(range(len(entries)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            if entries[j][2] != entries[i][2] or entries[j][1] - entries[i][1] > duration_tolerance:
                break
            if entries[i][2] == "v":
                ok = video_distance(entries[i][3], entries[j][3]) <= video_threshold
            else:
                ok = audio_bit_error_rate(entries[i][3], entries[j][3]) <= audio_max_ber
            if ok:
                parent[find(i)] = find(j)
    clusters: dict[int, list[FileInfo]] = {}
    for i, e in enumerate(entries):
        clusters.setdefault(find(i), []).append(e[0])
    out = [
        DupGroup("similar_media", f"media-{k}", sorted(v, key=lambda f: f.path), "possible")
        for k, v in clusters.items() if len(v) > 1
    ]
    out.sort(key=lambda g: g.reclaimable, reverse=True)
    return out
