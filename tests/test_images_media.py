import os
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from dupescout.core.detectors.image import find_similar_images
from dupescout.core.detectors.media import audio_bit_error_rate, find_similar_media, probe_duration, tools_available
from dupescout.core.perceptual import cluster_hashes, hamming
from dupescout.core.scanner import scan_roots


def pattern(seed, size=256):
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 255, (8, 8, 3), dtype=np.uint8)
    return Image.fromarray(blocks).resize((size, size), Image.NEAREST)


def test_similar_images_finds_resized_and_recompressed(make, cache):
    base = pattern(1)
    os.makedirs(os.path.join(make.base, "a"))
    os.makedirs(os.path.join(make.base, "b"))
    p1 = os.path.join(make.base, "a/orig.png")
    base.save(p1)
    small = os.path.join(make.base, "b/small.jpg")
    base.resize((100, 100)).save(small, "JPEG", quality=40)
    other = os.path.join(make.base, "b/other.png")
    pattern(2).save(other)
    rot = os.path.join(make.base, "b/rot.jpg")
    base.save(rot, "JPEG")
    files = scan_roots([make.base]).files
    groups = find_similar_images(files, cache, threshold=6)
    assert len(groups) == 1
    assert {os.path.basename(f.path) for f in groups[0].files} == {"orig.png", "small.jpg", "rot.jpg"}


def test_threshold_zero_only_identical_hash(make, cache):
    base = pattern(3)
    base.save(os.path.join(make.base, "a.png"))
    base.save(os.path.join(make.base, "b.png"))
    pattern(4).save(os.path.join(make.base, "c.png"))
    groups = find_similar_images(scan_roots([make.base]).files, cache, threshold=0)
    assert len(groups) == 1 and len(groups[0].files) == 2


def test_cluster_hashes_matches_bruteforce():
    rng = np.random.default_rng(0)
    items = {}
    base = [int(x) for x in rng.integers(0, 2**63, 40, dtype=np.int64)]
    for i, b in enumerate(base):
        items[f"{i}"] = b
        for j in range(2):  # twee buren met ≤3 bit-verschil
            v = b
            for bit in rng.choice(64, size=int(rng.integers(0, 4)), replace=False):
                v ^= 1 << int(bit)
            items[f"{i}n{j}"] = v
    for thr in (0, 3, 6):
        got = {frozenset(c) for c in cluster_hashes(items, thr)}
        # brute force met union-find
        keys = list(items)
        parent = {k: k for k in keys}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if hamming(items[a], items[b]) <= thr:
                    parent[find(a)] = find(b)
        comp = {}
        for k in keys:
            comp.setdefault(find(k), set()).add(k)
        want = {frozenset(v) for v in comp.values() if len(v) > 1}
        assert got == want, thr


def test_corrupt_image_reported_not_fatal(make, cache):
    make("kapot.jpg", b"geen afbeelding")
    pattern(5).save(os.path.join(make.base, "ok.png"))
    errors = []
    find_similar_images(scan_roots([make.base]).files, cache, errors=errors)
    assert any("kapot.jpg" in e for e in errors)


def test_audio_ber():
    assert audio_bit_error_rate([1, 2, 3], [1, 2, 3]) == 0
    assert audio_bit_error_rate([0], [0xFFFFFFFF]) == 1


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg ontbreekt")
def test_similar_video(make, cache):
    d = make.base
    def mk(name, extra):
        out = os.path.join(d, name)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=160x120:rate=10"] + extra + [out], check=True)
        return out
    mk("a.mp4", ["-c:v", "libx264", "-crf", "18"])
    mk("b.mp4", ["-c:v", "libx264", "-crf", "35"])           # andere kwaliteit, zelfde beeld
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "mandelbrot=size=160x120:rate=10", "-t", "4", "-c:v", "libx264", os.path.join(d, "c.mp4")], check=True)
    assert abs(probe_duration(os.path.join(d, "a.mp4")) - 4) < 0.5
    groups = find_similar_media(scan_roots([d]).files, cache)
    names = [{os.path.basename(f.path) for f in g.files} for g in groups]
    assert {"a.mp4", "b.mp4"} in names
    assert not any("c.mp4" in n and len(n) > 1 for n in names)
