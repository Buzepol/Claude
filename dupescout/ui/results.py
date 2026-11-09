"""Tab 2: resultaten bekijken, vergelijken, markeren en naar quarantaine."""

from __future__ import annotations

import os
import time
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import core
from ..core import quarantine as q
from ..core.db import Cache
from ..core.engine import DetectResult
from ..core.models import DupGroup, FileInfo, human_size
from ..core.selection import STRATEGIES, auto_select_all, build_requests, resolution_lookup
from ..core.thumbs import thumbnail_path
from .common import Worker, confirm, info, warn
from .preview import ComparePanel

KIND_NAMES = {
    "exact": "Exact identiek",
    "similar_image": "Gelijkende foto's",
    "name_size": "Zelfde naam + grootte (mogelijk)",
    "similar_media": "Gelijkende video/audio",
}
KIND_COLORS = {"certain": "zeker", "probable": "waarschijnlijk", "possible": "mogelijk"}


class ResultsTree(QTreeWidget):
    toggleRequested = Signal()
    markNextRequested = Signal()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        cur = self.currentItem()
        if e.key() == Qt.Key.Key_Space and cur is not None and cur.parent() is not None:
            self.toggleRequested.emit()
            return
        if e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and cur is not None and cur.parent() is not None:
            self.markNextRequested.emit()
            return
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and cur is not None:
            par = cur.parent()
            if par is None:
                if e.key() == Qt.Key.Key_Right:
                    self.expandItem(cur)
                    if cur.childCount():
                        self.setCurrentItem(cur.child(0))
                return
            i = par.indexOfChild(cur) + (1 if e.key() == Qt.Key.Key_Right else -1)
            if 0 <= i < par.childCount():
                self.setCurrentItem(par.child(i))
            return
        super().keyPressEvent(e)


class ResultsTab(QWidget):
    quarantineChanged = Signal()

    def __init__(self, cache: Cache) -> None:
        super().__init__()
        self.cache = cache
        self.groups: list[DupGroup] = []
        self.marks: set[str] = set()
        self._building = False
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Toon:"))
        self.kind = QComboBox()
        self.kind.currentIndexChanged.connect(self.rebuild)
        top.addWidget(self.kind)
        top.addSpacing(12)
        top.addWidget(QLabel("Automatisch:"))
        self.strategy = QComboBox()
        for k, v in STRATEGIES.items():
            self.strategy.addItem(v, k)
        top.addWidget(self.strategy)
        self.b_prefer = QPushButton("Voorkeursmap…")
        self.b_prefer.setToolTip("Bestanden in deze map(pen) blijven bij voorkeur staan")
        self.b_prefer.clicked.connect(self.pick_prefer)
        top.addWidget(self.b_prefer)
        self.prefer: list[str] = []
        b_auto = QPushButton("Selecteer duplicaten")
        b_auto.setToolTip("Markeer in elke groep alles behalve één bestand om te behouden")
        b_auto.clicked.connect(self.auto_mark)
        b_clear = QPushButton("Wis selectie")
        b_clear.clicked.connect(self.clear_marks)
        top.addWidget(b_auto)
        top.addWidget(b_clear)
        top.addStretch(1)
        lay.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.tree = ResultsTree()
        self.tree.setHeaderLabels(["Groep / bestand  (☑ = naar quarantaine)", "Grootte", "Gewijzigd", "Map"])
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 120)
        self.tree.setUniformRowHeights(True)
        self.tree.itemExpanded.connect(self._populate)
        self.tree.currentItemChanged.connect(self._current_changed)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.toggleRequested.connect(self.toggle_current)
        self.tree.markNextRequested.connect(self.mark_and_next)
        ll.addWidget(self.tree, 1)
        hint = QLabel("Sneltoetsen: ←/→ vorige/volgende foto in groep · spatie = markeer/ontmarkeer · ⌫ = markeer en volgende")
        hint.setStyleSheet("color: palette(mid);")
        hint.setWordWrap(True)
        ll.addWidget(hint)
        split.addWidget(left)
        self.panel = ComparePanel()
        split.addWidget(self.panel)
        split.setSizes([600, 680])
        lay.addWidget(split, 1)

        bot = QHBoxLayout()
        self.summary = QLabel("")
        bot.addWidget(self.summary, 1)
        self.b_go = QPushButton("Naar quarantaine…")
        self.b_go.setEnabled(False)
        self.b_go.clicked.connect(self.to_quarantine)
        bot.addWidget(self.b_go)
        lay.addLayout(bot)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.worker: Optional[Worker] = None

    # ---- data -------------------------------------------------------------
    def set_result(self, res: DetectResult) -> None:
        self.groups = list(res.groups)
        self.marks.clear()
        self.kind.blockSignals(True)
        self.kind.clear()
        self.kind.addItem(f"Alles ({len(self.groups)})", None)
        for k, name in KIND_NAMES.items():
            n = sum(1 for g in self.groups if g.kind == k)
            if n:
                self.kind.addItem(f"{name} ({n})", k)
        self.kind.blockSignals(False)
        self.rebuild()

    def _visible_groups(self) -> list[DupGroup]:
        k = self.kind.currentData()
        return [g for g in self.groups if k is None or g.kind == k]

    def rebuild(self) -> None:
        self._building = True
        self.tree.clear()
        self.panel.clear()
        for gi, g in enumerate(self.groups):
            if self.kind.currentData() not in (None, g.kind):
                continue
            it = QTreeWidgetItem(self.tree)
            it.setData(0, Qt.ItemDataRole.UserRole, ("group", gi))
            self._set_group_text(it, g)
            QTreeWidgetItem(it)  # plaatsvervanger voor lazy laden
        self._building = False
        self._update_summary()

    def _set_group_text(self, it: QTreeWidgetItem, g: DupGroup) -> None:
        n_marked = sum(1 for f in g.files if f.path in self.marks)
        label = f"{KIND_NAMES.get(g.kind, g.kind)} · {len(g.files)} bestanden · {KIND_COLORS.get(g.confidence, '')}"
        if n_marked:
            label += f" · {n_marked} gemarkeerd"
        it.setText(0, label)
        it.setText(1, human_size(g.reclaimable))
        it.setText(3, os.path.basename(g.files[0].path))

    def _group_of(self, it: QTreeWidgetItem) -> Optional[tuple[int, DupGroup]]:
        top = it if it.parent() is None else it.parent()
        data = top.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != "group":
            return None
        return data[1], self.groups[data[1]]

    def _populate(self, it: QTreeWidgetItem) -> None:
        if it.parent() is not None:
            return
        g = self._group_of(it)
        if not g or (it.childCount() and it.child(0).data(0, Qt.ItemDataRole.UserRole)):
            return
        grp = g[1]
        self._building = True
        it.takeChildren()
        for f in grp.files:
            c = QTreeWidgetItem(it)
            c.setData(0, Qt.ItemDataRole.UserRole, ("file", f))
            c.setText(0, f.name)
            c.setText(1, human_size(f.size))
            c.setText(2, time.strftime("%Y-%m-%d %H:%M", time.localtime(f.mtime_ns / 1e9)))
            c.setText(3, os.path.dirname(f.path))
            c.setFlags(c.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            c.setCheckState(0, Qt.CheckState.Checked if f.path in self.marks else Qt.CheckState.Unchecked)
            thumb = thumbnail_path(f.path, 64) if len(grp.files) <= 12 else None
            if thumb:
                from PySide6.QtGui import QIcon
                c.setIcon(0, QIcon(thumb))
        self._building = False

    # ---- selectie ---------------------------------------------------------
    def _current_changed(self, cur: Optional[QTreeWidgetItem], _prev) -> None:
        if cur is None:
            return
        g = self._group_of(cur)
        if not g:
            return
        first = None
        data = cur.data(0, Qt.ItemDataRole.UserRole)
        if data and data[0] == "file":
            first = data[1].path
        self.panel.show_files(g[1].files, self._panel_marks(g[1]), first)

    def _panel_marks(self, g: DupGroup) -> dict[str, str]:
        if not any(f.path in self.marks for f in g.files):
            return {}
        return {f.path: ("delete" if f.path in self.marks else "keep") for f in g.files}

    def _item_changed(self, it: QTreeWidgetItem, col: int) -> None:
        if self._building or col != 0:
            return
        data = it.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != "file":
            return
        f: FileInfo = data[1]
        g = self._group_of(it)
        if not g:
            return
        if it.checkState(0) == Qt.CheckState.Checked:
            others_kept = [x for x in g[1].files if x.path != f.path and x.path not in self.marks]
            if not others_kept:
                self._building = True
                it.setCheckState(0, Qt.CheckState.Unchecked)
                self._building = False
                self.status.setText("Minstens één kopie per groep blijft altijd staan.")
                return
            self.marks.add(f.path)
        else:
            self.marks.discard(f.path)
        self._refresh_group(it)

    def _refresh_group(self, it: QTreeWidgetItem) -> None:
        g = self._group_of(it)
        top = it if it.parent() is None else it.parent()
        if g:
            self._set_group_text(top, g[1])
            self.panel.show_files(g[1].files, self._panel_marks(g[1]), None)
        self._update_summary()

    def toggle_current(self) -> None:
        it = self.tree.currentItem()
        if it and it.parent() is not None:
            it.setCheckState(0, Qt.CheckState.Unchecked if it.checkState(0) == Qt.CheckState.Checked else Qt.CheckState.Checked)

    def mark_and_next(self) -> None:
        it = self.tree.currentItem()
        if it and it.parent() is not None:
            if it.checkState(0) != Qt.CheckState.Checked:
                it.setCheckState(0, Qt.CheckState.Checked)
            par = it.parent()
            i = par.indexOfChild(it) + 1
            if i < par.childCount():
                self.tree.setCurrentItem(par.child(i))

    def pick_prefer(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Map waarvan bestanden blijven staan")
        if d:
            self.prefer.append(os.path.realpath(d))
            self.b_prefer.setText(f"Voorkeursmap… ({len(self.prefer)})")
            self.status.setText("Voorkeursmappen: " + ", ".join(self.prefer))

    def auto_mark(self) -> None:
        strategy = self.strategy.currentData()
        resolution = resolution_lookup(self.cache) if strategy == "highest_resolution" else None
        visible = self._visible_groups()
        for g in visible:
            for f in g.files:
                self.marks.discard(f.path)
        self.marks |= auto_select_all(visible, strategy, self.prefer, resolution)
        self.rebuild()
        self.status.setText("Duplicaten geselecteerd (zelfde naam+grootte-groepen zijn nooit automatisch geselecteerd). Controleer het resultaat voordat je verplaatst.")

    def clear_marks(self) -> None:
        self.marks.clear()
        self.rebuild_keep_view()

    def rebuild_keep_view(self) -> None:
        self.rebuild()

    def _update_summary(self) -> None:
        sel = [f for g in self.groups for f in g.files if f.path in self.marks]
        seen: set[str] = set()
        uniq = [f for f in sel if not (f.path in seen or seen.add(f.path))]
        total = sum(f.size for f in uniq)
        self.summary.setText(f"{len(uniq)} bestanden gemarkeerd · {human_size(total)} komt vrij" if uniq else "Niets gemarkeerd")
        self.b_go.setEnabled(bool(uniq) and self.worker is None)

    # ---- uitvoeren --------------------------------------------------------
    def to_quarantine(self) -> None:
        requests, skipped = build_requests(self.groups, self.marks)
        if skipped:
            warn(self, "Niet toegestaan", f"In {len(skipped)} groep(en) zou geen enkele kopie overblijven; die worden overgeslagen.")
        if not requests:
            return
        total = sum(os.path.getsize(r.path) for r in requests if os.path.exists(r.path))
        if not confirm(
            self, "Naar quarantaine",
            f"{len(requests)} bestanden ({human_size(total)}) worden verplaatst naar de map .dupescout-quarantine op dezelfde schijf.\n\n"
            "Er wordt niets definitief gewist: je kunt alles herstellen of zelf wissen in het tabblad ‘Quarantaine’.",
            "Naar quarantaine",
        ):
            return
        self.worker = Worker(lambda control, progress: q.move_to_quarantine(requests, self.cache), self)
        self.worker.result.connect(self._moved)
        self.worker.failed.connect(lambda m: (self._end_work(), warn(self, "Mislukt", m)))
        self.b_go.setEnabled(False)
        self.status.setText("Bezig met controleren en verplaatsen…")
        self.worker.start()

    def _end_work(self) -> None:
        self.worker = None
        self._update_summary()

    def _moved(self, res: q.BatchResult) -> None:
        moved = set(res.moved)
        for g in self.groups:
            g.files = [f for f in g.files if f.path not in moved]
        self.groups = [g for g in self.groups if len(g.files) > 1]
        self.marks -= moved
        self.rebuild()
        self._end_work()
        msg = f"{len(res.moved)} bestanden naar quarantaine ({human_size(res.bytes_moved)})."
        if res.failed:
            msg += f" {len(res.failed)} niet verplaatst: " + "; ".join(f"{os.path.basename(p)} ({why})" for p, why in res.failed[:3])
        self.status.setText(msg)
        self.quarantineChanged.emit()
