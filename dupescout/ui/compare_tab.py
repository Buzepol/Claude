"""Tab 3: twee mappen vergelijken (A = referentie, B = controleren)."""

from __future__ import annotations

import csv
import os
import time
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QSplitter, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import quarantine as q
from ..core.compare import CompareResult, compare_folders
from ..core.db import Cache
from ..core.models import FileInfo, human_size
from ..core.scanner import ScanOptions
from .common import Worker, confirm, info, stage_text, warn
from .preview import ComparePanel


def _date(f: FileInfo) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(f.mtime_ns / 1e9))


class CompareTab(QWidget):
    quarantineChanged = Signal()

    def __init__(self, cache: Cache) -> None:
        super().__init__()
        self.cache = cache
        self.result: Optional[CompareResult] = None
        self.worker: Optional[Worker] = None
        lay = QVBoxLayout(self)

        for label, attr in (("Map A (referentie, blijft altijd ongewijzigd):", "edit_a"), ("Map B (te controleren):", "edit_b")):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            e = QLineEdit()
            setattr(self, attr, e)
            row.addWidget(e, 1)
            b = QPushButton("Bladeren…")
            b.clicked.connect(lambda _=False, e=e: self._browse(e))
            row.addWidget(b)
            lay.addLayout(row)

        ctl = QHBoxLayout()
        self.b_run = QPushButton("Vergelijken")
        self.b_run.clicked.connect(self.run)
        self.b_stop = QPushButton("Stoppen")
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(lambda: self.worker and self.worker.control.cancel())
        self.b_csv = QPushButton("Exporteer CSV…")
        self.b_csv.setEnabled(False)
        self.b_csv.clicked.connect(self.export_csv)
        ctl.addWidget(self.b_run)
        ctl.addWidget(self.b_stop)
        ctl.addWidget(self.b_csv)
        ctl.addStretch(1)
        lay.addLayout(ctl)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        lay.addWidget(self.bar)
        self.status = QLabel("Kies twee mappen (ook op verschillende schijven).")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.tabs = QTabWidget()
        self.t_both = QTreeWidget()
        self.t_both.setHeaderLabels(["Bestand in B (☑ = naar quarantaine)", "Gelijk aan (in A)", "Grootte"])
        self.t_a = QTreeWidget()
        self.t_a.setHeaderLabels(["Pad in A", "Grootte", "Gewijzigd"])
        self.t_b = QTreeWidget()
        self.t_b.setHeaderLabels(["Pad in B", "Grootte", "Gewijzigd"])
        for t in (self.t_both, self.t_a, self.t_b):
            t.setUniformRowHeights(True)
            t.setRootIsDecorated(False)
            t.currentItemChanged.connect(self._show)
        self.t_both.itemChanged.connect(lambda *_: self._summary())
        self.tabs.addTab(self.t_both, "In beide")
        self.tabs.addTab(self.t_a, "Alleen in A")
        self.tabs.addTab(self.t_b, "Alleen in B")
        split.addWidget(self.tabs)
        self.panel = ComparePanel()
        split.addWidget(self.panel)
        split.setSizes([560, 700])
        lay.addWidget(split, 1)

        bot = QHBoxLayout()
        self.summary = QLabel("")
        bot.addWidget(self.summary, 1)
        self.b_go = QPushButton("Duplicaten in B naar quarantaine…")
        self.b_go.setEnabled(False)
        self.b_go.clicked.connect(self.to_quarantine)
        bot.addWidget(self.b_go)
        lay.addLayout(bot)

    def _browse(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Kies map", edit.text() or ("/Volumes" if os.path.isdir("/Volumes") else os.path.expanduser("~")))
        if d:
            edit.setText(d)

    def run(self) -> None:
        a, b = self.edit_a.text().strip(), self.edit_b.text().strip()
        if not (os.path.isdir(a) and os.path.isdir(b)):
            warn(self, "Mappen kiezen", "Kies twee bestaande mappen.")
            return
        self.worker = Worker(lambda control, progress: compare_folders(a, b, self.cache, ScanOptions(), control, progress), self)
        self.worker.progress.connect(lambda s, d, t, det: (self.bar.setRange(0, t or 0), self.bar.setValue(d), self.status.setText(stage_text(s, d, t, det))))
        self.worker.result.connect(self._done)
        self.worker.failed.connect(lambda m: (self._reset(), warn(self, "Vergelijken mislukt", m)))
        self.worker.cancelled.connect(lambda: (self._reset(), self.status.setText("Gestopt.")))
        self.b_run.setEnabled(False)
        self.b_stop.setEnabled(True)
        self.worker.start()

    def _reset(self) -> None:
        self.worker = None
        self.b_run.setEnabled(True)
        self.b_stop.setEnabled(False)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)

    def _done(self, res: CompareResult) -> None:
        self._reset()
        self.result = res
        self._fill()
        self.b_csv.setEnabled(True)
        extra = f", {len(res.linked)} hardlinks tussen A en B (geen duplicaat)" if res.linked else ""
        self.status.setText(
            f"In beide: {sum(len(m.b_files) for m in res.in_both)} bestanden in B · alleen in A: {len(res.only_a)} · "
            f"alleen in B: {len(res.only_b)} · zelfde pad, andere inhoud: {len(res.changed_paths)}{extra}"
        )

    def _fill(self) -> None:
        res = self.result
        assert res is not None
        ra, rb = os.path.realpath(self.edit_a.text().strip()), os.path.realpath(self.edit_b.text().strip())
        rel = lambda f, root: os.path.relpath(f.path, root)  # noqa: E731
        self.t_both.blockSignals(True)
        self.t_both.clear()
        for m in res.in_both:
            for fb in m.b_files:
                it = QTreeWidgetItem(self.t_both, [rel(fb, rb), rel(m.a_files[0], ra) + (f" (+{len(m.a_files) - 1})" if len(m.a_files) > 1 else ""), human_size(fb.size)])
                it.setToolTip(0, fb.path)
                it.setToolTip(1, m.a_files[0].path)
                it.setData(0, Qt.ItemDataRole.UserRole, (m, fb))
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked)
        self.t_both.blockSignals(False)
        for tree, files, root in ((self.t_a, res.only_a, ra), (self.t_b, res.only_b, rb)):
            tree.clear()
            for f in files:
                it = QTreeWidgetItem(tree, [rel(f, root), human_size(f.size), _date(f)])
                it.setToolTip(0, f.path)
                it.setData(0, Qt.ItemDataRole.UserRole, (None, f))
        self.tabs.setTabText(0, f"In beide ({sum(len(m.b_files) for m in res.in_both)})")
        self.tabs.setTabText(1, f"Alleen in A ({len(res.only_a)})")
        self.tabs.setTabText(2, f"Alleen in B ({len(res.only_b)})")
        for t in (self.t_both, self.t_a, self.t_b):
            for c in range(t.columnCount()):
                t.resizeColumnToContents(c)
        self._summary()

    def _checked(self) -> list[tuple[object, FileInfo]]:
        out = []
        for i in range(self.t_both.topLevelItemCount()):
            it = self.t_both.topLevelItem(i)
            if it.checkState(0) == Qt.CheckState.Checked:
                out.append(it.data(0, Qt.ItemDataRole.UserRole))
        return out

    def _summary(self) -> None:
        sel = self._checked()
        total = sum(fb.size for _m, fb in sel)
        self.summary.setText(f"{len(sel)} duplicaten in B geselecteerd · {human_size(total)} komt vrij" if sel else "Geen duplicaten in B geselecteerd")
        self.b_go.setEnabled(bool(sel))

    def _show(self, cur: Optional[QTreeWidgetItem], _prev) -> None:
        if cur is None:
            return
        m, f = cur.data(0, Qt.ItemDataRole.UserRole)
        if m is not None:
            files = [m.a_files[0], f]
            marks = {m.a_files[0].path: "keep", f.path: "delete"}
        else:
            files, marks = [f], {}
        self.panel.show_files(files, marks)

    def to_quarantine(self) -> None:
        sel = self._checked()
        if not sel:
            return
        reqs = [q.QuarantineRequest(fb.path, [fa.path for fa in m.a_files], "duplicaat van map A") for m, fb in sel]
        total = sum(fb.size for _m, fb in sel)
        if not confirm(
            self, "Duplicaten in B naar quarantaine",
            f"{len(reqs)} bestanden in map B ({human_size(total)}) die ook in map A staan, worden naar quarantaine verplaatst.\n"
            "Map A blijft onaangeroerd. Je kunt alles herstellen via het tabblad ‘Quarantaine’.",
            "Naar quarantaine",
        ):
            return
        self.worker = Worker(lambda control, progress: q.move_to_quarantine(reqs, self.cache), self)
        self.worker.result.connect(self._moved)
        self.worker.failed.connect(lambda m: (self._reset(), warn(self, "Mislukt", m)))
        self.b_go.setEnabled(False)
        self.worker.start()

    def _moved(self, res: q.BatchResult) -> None:
        self._reset()
        moved = set(res.moved)
        if self.result:
            for m in self.result.in_both:
                m.b_files = [f for f in m.b_files if f.path not in moved]
            self.result.in_both = [m for m in self.result.in_both if m.b_files]
            self._fill()
        msg = f"{len(res.moved)} bestanden naar quarantaine ({human_size(res.bytes_moved)})."
        if res.failed:
            msg += f" {len(res.failed)} niet verplaatst: " + "; ".join(f"{os.path.basename(p)} ({why})" for p, why in res.failed[:3])
        self.status.setText(msg)
        self.quarantineChanged.emit()

    def export_csv(self) -> None:
        if not self.result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Rapport opslaan", "vergelijking.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["categorie", "pad_a", "pad_b", "grootte"])
            for m in self.result.in_both:
                for fb in m.b_files:
                    w.writerow(["in_beide", m.a_files[0].path, fb.path, fb.size])
            for f in self.result.only_a:
                w.writerow(["alleen_a", f.path, "", f.size])
            for f in self.result.only_b:
                w.writerow(["alleen_b", "", f.path, f.size])
        info(self, "Opgeslagen", path)
