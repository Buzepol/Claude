"""Tab 5: quarantaine beheren (herstellen / handmatig wissen) en logboek."""

from __future__ import annotations

import json
import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..core import quarantine as q
from ..core.db import Cache
from ..core.drives import list_external_volumes
from ..core.models import human_size
from .common import confirm, info


class QuarantineTab(QWidget):
    def __init__(self, cache: Cache) -> None:
        super().__init__()
        self.cache = cache
        self.batches: list[q.Batch] = []
        self.extra_mounts: list[str] = []
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)

        page = QWidget()
        lay = QVBoxLayout(page)
        note = QLabel("Hier staan bestanden die naar quarantaine zijn verplaatst. Er wordt niets automatisch gewist: "
                      "definitief wissen gebeurt alleen hier, handmatig, na bevestiging.")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        b_ref = QPushButton("Vernieuwen")
        b_ref.clicked.connect(self.refresh)
        b_add = QPushButton("Schijf met quarantaine toevoegen…")
        b_add.clicked.connect(self.add_mount)
        row.addWidget(b_ref)
        row.addWidget(b_add)
        row.addStretch(1)
        lay.addLayout(row)
        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Batch", "Datum", "Schijf", "Bestanden", "Grootte"])
        self.tree.setRootIsDecorated(False)
        self.tree.currentItemChanged.connect(self._show_items)
        split.addWidget(self.tree)
        self.items = QTreeWidget()
        self.items.setHeaderLabels(["Oorspronkelijke locatie", "Grootte", "Reden"])
        self.items.setRootIsDecorated(False)
        self.items.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        split.addWidget(self.items)
        lay.addWidget(split, 1)
        self.total = QLabel("")
        lay.addWidget(self.total)
        act = QHBoxLayout()
        self.b_restore = QPushButton("Herstel geselecteerde bestanden (of hele batch)")
        self.b_restore.clicked.connect(self.restore)
        self.b_purge = QPushButton("Definitief wissen…")
        self.b_purge.setStyleSheet("color: #c0392b;")
        self.b_purge.clicked.connect(self.purge)
        act.addWidget(self.b_restore)
        act.addWidget(self.b_purge)
        act.addStretch(1)
        lay.addLayout(act)
        tabs.addTab(page, "Quarantaine")

        self.log = QTreeWidget()
        self.log.setHeaderLabels(["Tijd", "Actie", "Details"])
        self.log.setRootIsDecorated(False)
        tabs.addTab(self.log, "Logboek")
        self.refresh()

    def add_mount(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Kies de schijf", "/Volumes" if os.path.isdir("/Volumes") else os.path.expanduser("~"))
        if d:
            self.extra_mounts.append(d)
            self.refresh()

    def refresh(self) -> None:
        mounts = tuple(self.extra_mounts) + tuple(list_external_volumes())
        self.batches = q.list_batches(self.cache, mounts)
        self.tree.clear()
        for b in self.batches:
            it = QTreeWidgetItem(self.tree, [b.batch_id, time.strftime("%Y-%m-%d %H:%M", time.localtime(b.created)),
                                             b.mount_point, str(b.stored_count), human_size(b.stored_bytes)])
            it.setData(0, Qt.ItemDataRole.UserRole, b)
        for c in range(5):
            self.tree.resizeColumnToContents(c)
        self.items.clear()
        n = sum(b.stored_count for b in self.batches)
        size = sum(b.stored_bytes for b in self.batches)
        self.total.setText(f"Totaal in quarantaine: {n} bestanden, {human_size(size)}")
        self.log.clear()
        for r in self.cache.logs(300):
            QTreeWidgetItem(self.log, [time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r["action"], r["detail"] or ""])
        for c in range(2):
            self.log.resizeColumnToContents(c)

    def _batch(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _show_items(self, *_):
        self.items.clear()
        b = self._batch()
        if not b:
            return
        for i in b.stored_items:
            it = QTreeWidgetItem(self.items, [i["original"], human_size(i["size"]), i["reason"]])
            it.setData(0, Qt.ItemDataRole.UserRole, i["id"])

    def _selected_ids(self, b: q.Batch):
        ids = [it.data(0, Qt.ItemDataRole.UserRole) for it in self.items.selectedItems()]
        return ids or None  # None = hele batch

    def restore(self) -> None:
        b = self._batch()
        if not b:
            return
        ids = self._selected_ids(b)
        done = q.restore(b, ids, self.cache)
        info(self, "Hersteld", f"{len(done)} bestanden teruggezet.")
        self.refresh()

    def purge(self) -> None:
        b = self._batch()
        if not b:
            return
        ids = self._selected_ids(b)
        sel = [i for i in b.stored_items if ids is None or i["id"] in ids]
        count, size = len(sel), sum(i["size"] for i in sel)
        if not count:
            return
        if not confirm(
            self, "Definitief wissen",
            f"{count} bestanden ({human_size(size)}) worden DEFINITIEF gewist. Dit kan niet ongedaan worden gemaakt.",
            "Definitief wissen",
        ):
            return
        q.purge(b, confirmed=True, item_ids=ids, cache=self.cache)
        self.refresh()
