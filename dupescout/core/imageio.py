"""Afbeeldingen openen (JPEG, PNG, HEIC, TIFF, RAW) voor hash, voorbeeld en thumbnails."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image, ImageOps

from .perceptual import phash_from_gray32

try:  # HEIC/HEIF
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optioneel
    pass

Image.MAX_IMAGE_PIXELS = None

RAW_EXTS = {"cr2", "cr3", "nef", "nrw", "arw", "dng", "orf", "rw2", "raf", "srw", "pef", "raw"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "bmp", "gif", "webp"} | RAW_EXTS


@dataclass
class ImageMeta:
    width: int
    height: int
    taken: Optional[str]  # "YYYY-MM-DD HH:MM:SS" of None
    camera: Optional[str] = None


def is_image(ext: str) -> bool:
    return ext.lower().lstrip(".") in IMAGE_EXTS


def _open(path: str) -> Image.Image:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in RAW_EXTS:
        try:
            import rawpy  # type: ignore

            with rawpy.imread(path) as raw:
                thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                return Image.open(io.BytesIO(thumb.data))
            return Image.fromarray(thumb.data)
        except ImportError:
            pass  # val terug op Pillow (werkt voor o.a. DNG/TIFF-gebaseerde RAW)
    return Image.open(path)


def _exif_date(exif: Image.Exif) -> Optional[str]:
    raw = None
    try:
        raw = exif.get_ifd(0x8769).get(0x9003) or exif.get_ifd(0x8769).get(0x9004)
    except Exception:
        pass
    raw = raw or exif.get(0x0132)
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", "ignore")
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip("\x00 ")
    # "2023:04:15 12:30:00" -> "2023-04-15 12:30:00"
    if len(raw) >= 19 and raw[4] == ":" and raw[7] == ":":
        return f"{raw[0:4]}-{raw[5:7]}-{raw[8:10]} {raw[11:19]}"
    return None


def read_meta(path: str) -> ImageMeta:
    with _open(path) as im:
        w, h = im.size
        exif = im.getexif()
        camera = " ".join(str(x).strip() for x in (exif.get(0x010F), exif.get(0x0110)) if x) or None
        return ImageMeta(w, h, _exif_date(exif), camera)


def compute_phash(path: str) -> tuple[int, ImageMeta]:
    """pHash + metadata. Gebruikt JPEG-draft-modus zodat grote foto's snel inlezen."""
    with _open(path) as im:
        w, h = im.size
        exif = im.getexif()
        taken = _exif_date(exif)
        camera = " ".join(str(x).strip() for x in (exif.get(0x010F), exif.get(0x0110)) if x) or None
        try:
            im.draft("L", (128, 128))
        except Exception:
            pass
        im = ImageOps.exif_transpose(im)
        gray = im.convert("L").resize((32, 32), Image.LANCZOS)
        return phash_from_gray32(np.asarray(gray)), ImageMeta(w, h, taken, camera)


def load_preview(path: str, max_side: int = 1600) -> Image.Image:
    """RGB-afbeelding (exif-gedraaid) begrensd tot max_side voor weergave."""
    with _open(path) as im:
        try:
            im.draft("RGB", (max_side, max_side))
        except Exception:
            pass
        out = ImageOps.exif_transpose(im).convert("RGB")
    out.thumbnail((max_side, max_side), Image.LANCZOS)
    return out
