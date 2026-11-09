"""Perceptuele hash (pHash via DCT) en snelle clustering op Hamming-afstand."""

from __future__ import annotations

from collections import defaultdict
from typing import Hashable, Mapping

import numpy as np


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    m[0, :] = np.sqrt(1.0 / n)
    return m.astype(np.float32)


_C = _dct_matrix(32)


def phash_from_gray32(arr: np.ndarray) -> int:
    """64-bit pHash uit een 32x32 grijswaardenarray."""
    a = np.asarray(arr, dtype=np.float32).reshape(32, 32)
    d = _C @ a @ _C.T
    low = d[:8, :8].flatten()
    med = float(np.median(low[1:]))
    bits = low > med
    value = 0
    for b in bits:
        value = (value << 1) | int(b)
    return value


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _popcount(x: np.ndarray) -> np.ndarray:
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x)
    table = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
    return table[x.view(np.uint8)].reshape(*x.shape, 8).sum(axis=-1)


class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def cluster_hashes(items: Mapping[Hashable, int], threshold: int) -> list[list[Hashable]]:
    """Groepeer sleutels waarvan de 64-bit hashes ≤ threshold bits verschillen.

    Multi-index: bij afstand ≤ k is minstens één van k+1 deelstukken identiek,
    dus alleen items met een gelijk deelstuk hoeven vergeleken te worden.
    """
    keys = list(items)
    by_hash: dict[int, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        by_hash[items[k]].append(i)
    uniq = list(by_hash)
    n = len(uniq)
    uf = _UF(n)
    if threshold > 0 and n > 1:
        arr = np.array(uniq, dtype=np.uint64)
        nchunks = threshold + 1
        for c in range(nchunks):
            lo, hi = c * 64 // nchunks, (c + 1) * 64 // nchunks
            mask = np.uint64((1 << (hi - lo)) - 1)
            chunk = (arr >> np.uint64(lo)) & mask
            order = np.argsort(chunk, kind="stable")
            sorted_chunk = chunk[order]
            cuts = np.flatnonzero(np.diff(sorted_chunk)) + 1
            for idx in np.split(order, cuts):
                m = len(idx)
                if m < 2:
                    continue
                sub = arr[idx]
                block = max(1, 2_000_000 // m)
                for s in range(0, m, block):
                    x = sub[s : s + block, None] ^ sub[None, :]
                    ii, jj = np.nonzero(_popcount(x) <= threshold)
                    for a, b in zip(ii.tolist(), jj.tolist()):
                        a += s
                        if a < b:
                            uf.union(int(idx[a]), int(idx[b]))
    clusters: dict[int, list[Hashable]] = defaultdict(list)
    for ui, h in enumerate(uniq):
        root = uf.find(ui)
        clusters[root].extend(keys[i] for i in by_hash[h])
    return [c for c in clusters.values() if len(c) > 1]
