import os
from datetime import datetime

import pytest
from PIL import Image

from dupescout.core.organizer import executor, rules as R
from dupescout.core.organizer.metadata import get_date
from dupescout.core.organizer.planner import build_plan, sanitize_component
from dupescout.core.drives import VolumeInfo
from dupescout.core.scanner import scan_roots


def jpeg_with_date(path, date="2021:07:04 10:20:30", size=(40, 30)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im = Image.new("RGB", size, (200, 10, 10))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = date
    im.save(path, "JPEG", exif=exif)
    return path


def test_date_sources(make):
    j = jpeg_with_date(os.path.join(make.base, "a.jpg"))
    assert get_date(j) == (datetime(2021, 7, 4, 10, 20, 30), "exif")
    n = make("IMG_20190315_120000.png", b"notimage")
    assert get_date(n) == (datetime(2019, 3, 15), "bestandsnaam")
    m = make("zonder.txt", b"x", mtime=1_600_000_000)
    d, src = get_date(m)
    assert src == "wijzigingsdatum" and d.year == 2020


def test_rules_parse_and_match():
    rules = R.load_rules(R.PRESET_PHOTOS)
    when = datetime(2022, 3, 5)
    assert R.first_match(rules, "x/a.JPG", 10, when).name == "Foto's op datum"
    assert R.first_match(rules, "x/a.pdf", 10, when).name == "Documenten per type"
    assert R.first_match(rules, "x/a.zzz", 10, when).name == "Overig"
    ctx = R.template_context("x/a.jpg", when)
    assert R.render("{jaar}/{maand:02d}-{maandnaam}", ctx) == "2022/03-maart"


def test_rules_conditions():
    rules = R.load_rules("""
- naam: groot
  als: {groter_dan: 1MB, naam_regex: '^Screenshot'}
  doel: "Groot"
- naam: rest
  doel: "Rest"
""")
    w = datetime(2020, 1, 1)
    assert R.first_match(rules, "Screenshot 1.png", 2 * 1024**2, w).name == "groot"
    assert R.first_match(rules, "Screenshot 1.png", 100, w).name == "rest"
    assert R.parse_size("1.5GB") == int(1.5 * 1024**3)


@pytest.mark.parametrize("bad", ["", "- naam: x", "- doel: '{onbekend}'", "- doel: x\n  als: {foo: 1}", "- doel: x\n  als: {naam_regex: '('}"])
def test_rules_errors(bad):
    with pytest.raises(R.RuleError):
        R.load_rules(bad)


def test_sanitize_for_filesystems():
    exfat = VolumeInfo("/v", "id", "exfat", True, False, "v")
    apfs = VolumeInfo("/v", "id", "apfs", True, False, "v")
    assert sanitize_component('a:b*c?', exfat) == ("a_b_c_", True)
    assert sanitize_component("a:b", apfs) == ("a-b", True)
    assert sanitize_component("ok", apfs) == ("ok", False)
    long = sanitize_component("x" * 300 + ".jpg", apfs)[0]
    assert len(long.encode()) <= 255 and long.endswith(".jpg")


def test_plan_is_dry_run_and_execute_undo(make, cache, tmp_path):
    jpeg_with_date(os.path.join(make.base, "in/a.jpg"), "2021:07:04 10:00:00")
    jpeg_with_date(os.path.join(make.base, "in/b.jpg"), "2022:01:09 10:00:00", size=(50, 20))
    make("in/doc.pdf", b"pdf", mtime=1_600_000_000)
    files = scan_roots([os.path.join(make.base, "in")]).files
    dest = str(tmp_path / "sorted")
    plan = build_plan(files, R.load_rules(R.PRESET_PHOTOS), dest)
    assert not os.path.exists(dest)  # voorstel raakt niets aan
    assert plan.summary()["ok"] == 3
    folders = plan.per_folder()
    assert "Foto's/2021/07-juli" in folders and "Foto's/2022/01-januari" in folders
    res = executor.execute(plan, cache)
    assert res.done == 3 and not res.failed
    assert os.path.exists(os.path.join(dest, "Foto's/2021/07-juli/a.jpg"))
    assert not os.path.exists(os.path.join(make.base, "in/a.jpg"))
    n, failed = executor.undo(res.batch_id, cache)
    assert n == 3 and not failed
    assert os.path.exists(os.path.join(make.base, "in/a.jpg"))
    assert not os.path.exists(os.path.join(dest, "Foto's/2021/07-juli/a.jpg"))


def test_plan_conflicts_and_duplicates(make, cache, tmp_path):
    jpeg_with_date(os.path.join(make.base, "x/a.jpg"), "2021:07:04 10:00:00", size=(40, 30))
    jpeg_with_date(os.path.join(make.base, "y/a.jpg"), "2021:07:04 11:00:00", size=(60, 30))  # zelfde naam, andere inhoud
    files = scan_roots([make.base]).files
    dest = str(tmp_path / "out")
    plan = build_plan(files, R.load_rules(R.PRESET_DATE), dest, "copy")
    dsts = sorted(os.path.basename(i.dst) for i in plan.active())
    assert dsts == ["a (2).jpg", "a.jpg"]
    executor.execute(plan, cache)
    # opnieuw plannen: nu staan de bestanden er al -> duplicaat
    plan2 = build_plan(files, R.load_rules(R.PRESET_DATE), dest, "copy")
    assert plan2.summary()["duplicate"] == 2 and plan2.active() == []


def test_copy_mode_keeps_source_and_undo_removes_copy(make, cache, tmp_path):
    jpeg_with_date(os.path.join(make.base, "s/a.jpg"))
    files = scan_roots([make.base]).files
    plan = build_plan(files, R.load_rules(R.PRESET_DATE), str(tmp_path / "o"), "copy")
    res = executor.execute(plan, cache)
    assert os.path.exists(files[0].path)
    n, failed = executor.undo(res.batch_id, cache)
    assert n == 1 and not failed and os.path.exists(files[0].path)
    assert not any(f.endswith("a.jpg") for _r, _d, fs in os.walk(tmp_path / "o") for f in fs if "quarantine" not in _r)
