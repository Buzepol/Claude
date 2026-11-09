import os

import pytest

from dupescout.core.compare import compare_folders


def test_compare_categories(make, cache):
    make("A/same.txt", b"same" * 100)
    make("B/renamed.txt", b"same" * 100)          # zelfde inhoud, andere naam
    make("A/onlya.txt", b"a" * 50)
    make("B/onlyb.txt", b"b" * 60)
    make("A/sub/changed.txt", b"v1" * 20)
    make("B/sub/changed.txt", b"v2" * 20)
    r = compare_folders(os.path.join(make.base, "A"), os.path.join(make.base, "B"), cache)
    assert len(r.in_both) == 1
    assert [os.path.basename(f.path) for f in r.in_both[0].b_files] == ["renamed.txt"]
    assert {os.path.basename(f.path) for f in r.only_a} == {"onlya.txt", "changed.txt"}
    assert {os.path.basename(f.path) for f in r.only_b} == {"onlyb.txt", "changed.txt"}
    assert len(r.changed_paths) == 1
    assert r.reclaimable == len(b"same" * 100)


def test_compare_multiple_in_b_all_listed(make, cache):
    make("A/x.bin", b"1" * 100)
    make("B/x1.bin", b"1" * 100)
    make("B/x2.bin", b"1" * 100)
    r = compare_folders(os.path.join(make.base, "A"), os.path.join(make.base, "B"), cache)
    assert len(r.in_both) == 1 and len(r.in_both[0].b_files) == 2


def test_compare_hardlink_between_a_and_b_not_duplicate(make, cache):
    a = make("A/x.bin", b"1" * 100)
    os.makedirs(os.path.join(make.base, "B"))
    os.link(a, os.path.join(make.base, "B", "x.bin"))
    r = compare_folders(os.path.join(make.base, "A"), os.path.join(make.base, "B"), cache)
    assert r.in_both == [] and len(r.linked) == 1


def test_compare_rejects_same_or_nested(make, cache):
    make("A/f.txt", b"x")
    a = os.path.join(make.base, "A")
    with pytest.raises(ValueError):
        compare_folders(a, a, cache)
    with pytest.raises(ValueError):
        compare_folders(a, os.path.join(a, "sub"), cache)
