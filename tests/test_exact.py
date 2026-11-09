import os

from dupescout.core.detectors.exact import find_exact
from dupescout.core.detectors.namesize import find_name_size, normalize_name
from dupescout.core.hashing import files_identical
from dupescout.core.scanner import scan_roots


def test_exact_groups_and_ignores_unique(make, cache):
    a = make("a/foto.jpg", b"A" * 300_000)
    b = make("b/foto kopie.jpg", b"A" * 300_000)
    make("c/other.jpg", b"B" * 300_000)          # zelfde grootte, andere inhoud
    make("d/unique.txt", b"hello")
    make("e/leeg1", b"")
    make("e/leeg2", b"")
    res = scan_roots([make.base])
    groups = find_exact(res.files, cache)
    assert len(groups) == 1
    assert {f.path for f in groups[0].files} == {a, b}
    assert groups[0].reclaimable == 300_000


def test_partial_hash_catches_difference_in_middle(make, cache):
    data = bytearray(b"Z" * 400_000)
    other = bytearray(data)
    other[200_000] = 0  # alleen midden verschilt: begin/einde gelijk
    make("a.bin", bytes(data))
    make("b.bin", bytes(other))
    groups = find_exact(scan_roots([make.base]).files, cache)
    assert groups == []  # volledige hash onderscheidt ze


def test_hardlinks_are_not_duplicates(make, cache):
    a = make("a.bin", b"H" * 5000)
    os.link(a, os.path.join(make.base, "b.bin"))
    groups = find_exact(scan_roots([make.base]).files, cache)
    assert groups == []


def test_cache_reused(make, cache):
    make("a.bin", b"Q" * 5000)
    make("b.bin", b"Q" * 5000)
    files = scan_roots([make.base]).files
    find_exact(files, cache)
    import dupescout.core.detectors.exact as ex
    called = []
    orig = ex.partial_hash
    ex.partial_hash = lambda *a, **k: called.append(1) or orig(*a, **k)
    try:
        assert len(find_exact(files, cache)) == 1
    finally:
        ex.partial_hash = orig
    assert called == []  # alles uit cache


def test_excludes_and_forbidden_dirs(make):
    make("Foto's.photoslibrary/originals/x.jpg", b"1" * 100)
    make("ok/x.jpg", b"1" * 100)
    make("ok/.DS_Store", b"1" * 100)
    make("ok/._x.jpg", b"1" * 100)
    make("skip/y.jpg", b"1" * 100)
    from dupescout.core.scanner import ScanOptions
    res = scan_roots([make.base], ScanOptions(excludes=["skip"]))
    assert [os.path.basename(f.path) for f in res.files] == ["x.jpg"]
    assert any(p.endswith(".photoslibrary") for p in res.skipped_libraries)


def test_symlinks_not_followed(make):
    a = make("a.txt", b"hi")
    os.symlink(a, os.path.join(make.base, "link.txt"))
    assert len(scan_roots([make.base]).files) == 1


def test_name_size():
    assert normalize_name("IMG_0001 (1)") == "img_0001"
    assert normalize_name("Foto - kopie 2") == "foto"
    assert normalize_name("report copy") == "report"


def test_name_size_groups(make):
    make("a/IMG_1.jpg", b"1234")
    make("b/IMG_1 (1).jpg", b"5678")
    make("c/IMG_2.jpg", b"1234")
    g = find_name_size(scan_roots([make.base]).files)
    assert len(g) == 1 and g[0].confidence == "possible" and len(g[0].files) == 2


def test_files_identical(make):
    a, b, c = make("a", b"x" * 10), make("b", b"x" * 10), make("c", b"y" * 10)
    assert files_identical(a, b) and not files_identical(a, c)
