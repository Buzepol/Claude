import os

import pytest

from dupescout.core import quarantine as q
from dupescout.core.scanner import scan_roots


def test_move_restore_roundtrip(make, cache):
    keep = make("a/keep.bin", b"K" * 1000)
    dup = make("b/dup.bin", b"K" * 1000)
    r = q.move_to_quarantine([q.QuarantineRequest(dup, [keep])], cache)
    assert r.failed == [] and not os.path.exists(dup) and os.path.exists(keep)
    batches = q.list_batches(cache)
    assert len(batches) == 1 and batches[0].stored_count == 1
    # de quarantainemap wordt niet meer gescand
    assert all(".dupescout-quarantine" not in f.path for f in scan_roots([make.base]).files)
    restored = q.restore(batches[0], cache=cache)
    assert restored == [dup] and open(dup, "rb").read() == b"K" * 1000
    assert q.list_batches(cache) == []


def test_never_deletes_last_copy(make, cache):
    a = make("a.bin", b"S" * 100)
    b = make("b.bin", b"S" * 100)
    # beide verwijderen, ieder met de ander als 'keeper' -> geweigerd
    r = q.move_to_quarantine([q.QuarantineRequest(a, [b]), q.QuarantineRequest(b, [a])], cache)
    assert r.moved == [] and len(r.failed) == 2
    assert os.path.exists(a) and os.path.exists(b)


def test_requires_keeper_and_identical_content(make, cache):
    a = make("a.bin", b"1" * 100)
    b = make("b.bin", b"2" * 100)
    r = q.move_to_quarantine([q.QuarantineRequest(a, [])], cache)
    assert r.failed and os.path.exists(a)
    r = q.move_to_quarantine([q.QuarantineRequest(a, [b])], cache)
    assert "wijkt af" in r.failed[0][1] and os.path.exists(a)
    r = q.move_to_quarantine([q.QuarantineRequest(a, [b], verify_identical=False)], cache)
    assert r.moved == [a]


def test_hardlink_keeper_is_not_a_keeper(make, cache):
    a = make("a.bin", b"L" * 100)
    b = os.path.join(make.base, "b.bin")
    os.link(a, b)
    r = q.move_to_quarantine([q.QuarantineRequest(b, [a])], cache)
    assert r.moved == []  # zelfde data: niets te winnen, niets verplaatsen


def test_restore_name_conflict(make, cache):
    keep = make("keep.bin", b"C" * 50)
    dup = make("dup.bin", b"C" * 50)
    q.move_to_quarantine([q.QuarantineRequest(dup, [keep])], cache)
    make("dup.bin", b"new")  # nieuw bestand op de oude plek
    b = q.list_batches(cache)[0]
    out = q.restore(b, cache=cache)
    assert out[0].endswith("dup (hersteld).bin") and open(dup, "rb").read() == b"new"


def test_purge_needs_explicit_confirmation(make, cache):
    keep = make("keep.bin", b"P" * 70)
    dup = make("dup.bin", b"P" * 70)
    q.move_to_quarantine([q.QuarantineRequest(dup, [keep])], cache)
    b = q.list_batches(cache)[0]
    stored = b.items[0]["stored"]
    with pytest.raises(q.QuarantineError):
        q.purge(b)
    with pytest.raises(q.QuarantineError):
        q.purge(b, confirmed="yes")  # alleen echt True telt
    assert os.path.exists(stored)
    n, size = q.purge(b, confirmed=True, cache=cache)
    assert (n, size) == (1, 70) and not os.path.exists(stored)
    assert q.list_batches(cache) == []


def test_nothing_is_purged_automatically(make, cache):
    keep = make("keep.bin", b"N" * 70)
    dup = make("dup.bin", b"N" * 70)
    q.move_to_quarantine([q.QuarantineRequest(dup, [keep])], cache)
    import time
    b = q.list_batches(cache)[0]
    # ook een heel oude batch blijft staan: geen timer-logica aanwezig
    b.created = time.time() - 10 * 365 * 86400
    assert q.list_batches(cache)[0].stored_count == 1


def test_sidecars_move_along(make, cache):
    keep = make("a/IMG_1.jpg", b"J" * 100)
    dup = make("b/IMG_1.jpg", b"J" * 100)
    side = make("b/IMG_1.xmp", b"<xmp/>")
    make("a/IMG_1.mov", b"M" * 10)     # keeper heeft Live Photo-video
    live = make("b/IMG_1.mov", b"M" * 10)
    r = q.move_to_quarantine([q.QuarantineRequest(dup, [keep])], cache)
    assert not os.path.exists(side) and not os.path.exists(live)
    b = q.list_batches(cache)[0]
    assert b.stored_count == 3
    q.restore(b, cache=cache)
    assert os.path.exists(side) and os.path.exists(live)


def test_live_video_stays_if_keeper_has_none(make, cache):
    keep = make("a/IMG_2.jpg", b"J" * 100)
    dup = make("b/IMG_2.jpg", b"J" * 100)
    live = make("b/IMG_2.mov", b"M" * 10)
    q.move_to_quarantine([q.QuarantineRequest(dup, [keep])], cache)
    assert os.path.exists(live)
