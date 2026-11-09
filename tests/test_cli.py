import os

from dupescout import cli
from dupescout.core import quarantine as q


def run(*args):
    return cli.main(list(args))


def test_scan_and_clean_flow(make, capsys):
    keep = make("a/one.bin", b"D" * 2000, mtime=1_500_000_000)
    dup = make("b/two.bin", b"D" * 2000, mtime=1_600_000_000)
    assert run("scan", make.base) == 0
    assert "1 groepen" in capsys.readouterr().out
    assert run("clean", make.base) == 0           # dry-run
    assert os.path.exists(dup)
    assert run("clean", make.base, "--apply") == 0  # oudste blijft
    assert os.path.exists(keep) and not os.path.exists(dup)
    assert run("quarantine", "list") == 0
    out = capsys.readouterr().out
    batch = [l.split()[0] for l in out.splitlines() if l[:2] == "20"][0]
    assert run("quarantine", "purge", batch) == 1   # geen bevestiging -> niets gewist
    assert run("quarantine", "restore", batch) == 0
    assert os.path.exists(dup)


def test_compare_apply(make):
    make("A/x.bin", b"1" * 500)
    dup = make("B/y.bin", b"1" * 500)
    only = make("B/z.bin", b"2" * 500)
    a, b = os.path.join(make.base, "A"), os.path.join(make.base, "B")
    assert run("compare", a, b, "--csv", os.path.join(make.base, "r.csv")) == 0
    assert os.path.exists(dup)
    assert run("compare", a, b, "--apply") == 0
    assert not os.path.exists(dup) and os.path.exists(only) and os.path.exists(os.path.join(a, "x.bin"))


def test_organize_cli(make, tmp_path):
    make("in/a.txt", b"hello", mtime=1_600_000_000)
    dest = str(tmp_path / "out")
    assert run("organize", os.path.join(make.base, "in"), "--dest", dest) == 0
    assert not os.path.exists(dest)
    assert run("organize", os.path.join(make.base, "in"), "--dest", dest, "--apply") == 0
    assert os.path.exists(os.path.join(dest, "2020", "09-september", "a.txt"))
