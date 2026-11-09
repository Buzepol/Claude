import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dupescout.core.db import Cache  # noqa: E402
from dupescout.ui import common  # noqa: E402
from dupescout.ui.app import MainWindow  # noqa: E402

SHOT = os.environ.get("DUPESCOUT_SHOTS")  # map voor screenshots (optioneel)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pattern(seed, size=300):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (6, 6, 3), dtype=np.uint8)).resize((size, size), Image.NEAREST)


def wait_for(cond, timeout=20000):
    waited = 0
    while not cond() and waited < timeout:
        QTest.qWait(50)
        waited += 50
    assert cond(), "time-out"


@pytest.fixture
def win(app, tmp_path, make, monkeypatch):
    monkeypatch.setattr(common, "confirm", lambda *a, **k: True)
    for mod in ("results", "compare_tab", "organize_tab", "quarantine_tab"):
        m = __import__(f"dupescout.ui.{mod}", fromlist=["x"])
        if hasattr(m, "confirm"):
            monkeypatch.setattr(m, "confirm", lambda *a, **k: True)
        if hasattr(m, "info"):
            monkeypatch.setattr(m, "info", lambda *a, **k: None)
        if hasattr(m, "warn"):
            monkeypatch.setattr(m, "warn", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    w = MainWindow(Cache(str(tmp_path / "ui.db")))
    w.resize(1280, 800)
    w.show()
    yield w
    w.close()


def shot(win, name):
    if SHOT:
        os.makedirs(SHOT, exist_ok=True)
        win.grab().save(os.path.join(SHOT, name + ".png"))


def test_scan_review_quarantine_restore(win, make, tmp_path):
    base = pattern(1)
    os.makedirs(os.path.join(make.base, "a"))
    os.makedirs(os.path.join(make.base, "b"))
    base.save(os.path.join(make.base, "a/foto.jpg"), "JPEG", quality=95)
    open(os.path.join(make.base, "b/foto kopie.jpg"), "wb").write(open(os.path.join(make.base, "a/foto.jpg"), "rb").read())
    base.resize((150, 150)).save(os.path.join(make.base, "b/klein.jpg"), "JPEG", quality=50)
    pattern(2).save(os.path.join(make.base, "a/andere.png"))

    s = win.sources
    s.add_path(make.base)
    s.slider.setValue(8)  # klein.jpg (q50, halve resolutie) wijkt 6 bits af van het origineel
    s.start()
    wait_for(lambda: win.tabs.currentWidget() is win.results)
    shot(win, "1-na-scan")
    r = win.results
    assert len(r.groups) == 2  # exact (2 bestanden) + gelijkende foto's (3 bestanden)
    kinds = sorted(g.kind for g in r.groups)
    assert kinds == ["exact", "similar_image"]

    # groep openen en eerste bestand tonen in het voorbeeldpaneel
    top = r.tree.topLevelItem(0)
    r.tree.expandItem(top)
    r.tree.setCurrentItem(top.child(0))
    QTest.qWait(300)
    assert len(r.panel._views) >= 2
    shot(win, "2-resultaten-voorbeeld")

    # sneltoetsen: rechts = volgend bestand; spatie = markeren
    r.tree.setFocus()
    QTest.keyClick(r.tree, Qt.Key.Key_Right)
    cur = r.tree.currentItem()
    assert cur.parent() is top
    QTest.keyClick(r.tree, Qt.Key.Key_Space)
    assert len(r.marks) == 1

    # laatste kopie kan niet gemarkeerd worden
    r.clear_marks()
    top = r.tree.topLevelItem(0)
    r.tree.expandItem(top)
    for i in range(top.childCount()):
        top.child(i).setCheckState(0, Qt.CheckState.Checked)
    assert len(r.marks) == top.childCount() - 1

    # automatisch selecteren en verplaatsen
    r.clear_marks()
    r.auto_mark()
    n = len(r.marks)
    assert n >= 2
    shot(win, "3-geselecteerd")
    r.to_quarantine()
    wait_for(lambda: r.worker is None)
    assert os.path.isdir(os.path.join(str(tmp_path), ".dupescout-quarantine"))
    win.quarantine.refresh()
    win.tabs.setCurrentWidget(win.quarantine)
    assert win.quarantine.tree.topLevelItemCount() == 1
    shot(win, "5-quarantaine")

    # herstellen
    win.quarantine.tree.setCurrentItem(win.quarantine.tree.topLevelItem(0))
    win.quarantine.restore()
    assert win.quarantine.tree.topLevelItemCount() == 0
    assert os.path.exists(os.path.join(make.base, "b/foto kopie.jpg"))


def test_compare_tab(win, make):
    make("A/x.bin", b"1" * 400)
    make("A/only.bin", b"3" * 400)
    make("B/y.bin", b"1" * 400)
    make("B/z.bin", b"2" * 400)
    c = win.compare
    win.tabs.setCurrentWidget(c)
    c.edit_a.setText(os.path.join(make.base, "A"))
    c.edit_b.setText(os.path.join(make.base, "B"))
    c.run()
    wait_for(lambda: c.result is not None)
    assert c.t_both.topLevelItemCount() == 1 and c.t_a.topLevelItemCount() == 1 and c.t_b.topLevelItemCount() == 1
    shot(win, "4-vergelijk")
    c.to_quarantine()
    wait_for(lambda: c.worker is None and c.result.in_both == [])
    assert not os.path.exists(os.path.join(make.base, "B/y.bin"))
    assert os.path.exists(os.path.join(make.base, "A/x.bin"))


def test_organize_tab(win, make, tmp_path):
    p = os.path.join(make.base, "in")
    os.makedirs(p)
    pattern(3).save(os.path.join(p, "IMG_20200102_101500.jpg"))
    o = win.organize
    win.tabs.setCurrentWidget(o)
    o.src.setText(p)
    o.dst.setText(str(tmp_path / "sorted"))
    o.make_plan()
    wait_for(lambda: o.plan is not None)
    assert not os.path.exists(tmp_path / "sorted")
    assert o.tree.topLevelItemCount() >= 2
    shot(win, "6-ordenen-voorbeeld")
    o.execute()
    wait_for(lambda: o.plan is None)
    assert os.path.exists(tmp_path / "sorted" / "Foto's" / "2020" / "01-januari" / "IMG_20200102_101500.jpg")
    assert o.undo_box.count() == 1
    o.undo()
    assert os.path.exists(os.path.join(p, "IMG_20200102_101500.jpg"))
