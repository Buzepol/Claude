"""Tab 4: ordenen in nieuwe mappen — altijd eerst een voorbeeld."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core.db import Cache
from ..core.models import human_size
from ..core.organizer import executor, rules as R
from ..core.organizer.planner import Plan, build_plan
from ..core.scanner import ScanOptions, scan_roots
from .common import Worker, confirm, info, stage_text, warn


class OrganizeTab(QWidget):
    quarantineChanged = Signal()

    def __init__(self, cache: Cache) -> None:
        super().__init__()
        self.cache = cache
        self.plan: Optional[Plan] = None
        self.worker: Optional[Worker] = None
        self._building = False
        lay = QVBoxLayout(self)

        for label, attr in (("Bronmap:", "src"), ("Doelmap (nieuw):", "dst")):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            e = QLineEdit()
            setattr(self, attr, e)
            row.addWidget(e, 1)
            b = QPushButton("Bladeren…")
            b.clicked.connect(lambda _=False, e=e: self._browse(e))
            row.addWidget(b)
            lay.addLayout(row)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Actie:"))
        self.mode = QComboBox()
        self.mode.addItem("Verplaatsen", "move")
        self.mode.addItem("Kopiëren (bron blijft staan)", "copy")
        opts.addWidget(self.mode)
        opts.addWidget(QLabel("Bij naamconflict:"))
        self.conflict = QComboBox()
        self.conflict.addItem("Nummeren (naam (2))", "number")
        self.conflict.addItem("Overslaan", "skip")
        opts.addWidget(self.conflict)
        opts.addStretch(1)
        lay.addLayout(opts)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        rb = QHBoxLayout()
        rb.addWidget(QLabel("Regels (YAML):"))
        for text, fn in (("Op datum", lambda: self.rules.setPlainText(R.PRESET_DATE)),
                         ("Foto’s / video’s / documenten", lambda: self.rules.setPlainText(R.PRESET_PHOTOS)),
                         ("Laden…", self.load_rules_file), ("Opslaan…", self.save_rules_file)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            rb.addWidget(b)
        rb.addStretch(1)
        ll.addLayout(rb)
        self.rules = QPlainTextEdit()
        f = QFont("Menlo")
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.rules.setFont(f)
        self.rules.setPlainText(R.PRESET_PHOTOS)
        ll.addWidget(self.rules, 1)
        help_ = QLabel("Velden: {jaar} {maand:02d} {maandnaam} {dag} {extensie} {naam} {bestandsnaam} {bron_map}. "
                       "Voorwaarden (als): extensie, mime, naam_regex, groter_dan, kleiner_dan, datum_voor, datum_na, bronmap, schijf. Eerste passende regel wint.")
        help_.setWordWrap(True)
        help_.setStyleSheet("color: palette(mid);")
        ll.addWidget(help_)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.b_plan = QPushButton("Voorbeeld maken")
        self.b_plan.clicked.connect(self.make_plan)
        rl.addWidget(self.b_plan)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Nieuwe structuur  (☑ = meenemen)", "Aantal / van", "Regel", "Datum uit"])
        self.tree.setColumnWidth(0, 360)
        self.tree.itemChanged.connect(self._changed)
        rl.addWidget(self.tree, 1)
        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet("color: #b9770e;")
        rl.addWidget(self.warnings)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        lay.addWidget(split, 1)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        lay.addWidget(self.bar)
        bot = QHBoxLayout()
        self.summary = QLabel("Maak eerst een voorbeeld; er wordt niets gewijzigd voordat je bevestigt.")
        self.summary.setWordWrap(True)
        bot.addWidget(self.summary, 1)
        self.b_go = QPushButton("Uitvoeren…")
        self.b_go.setEnabled(False)
        self.b_go.clicked.connect(self.execute)
        bot.addWidget(self.b_go)
        lay.addLayout(bot)
        undo = QHBoxLayout()
        undo.addWidget(QLabel("Ongedaan maken:"))
        self.undo_box = QComboBox()
        undo.addWidget(self.undo_box, 1)
        self.b_undo = QPushButton("Maak ongedaan")
        self.b_undo.clicked.connect(self.undo)
        undo.addWidget(self.b_undo)
        lay.addLayout(undo)
        self.refresh_batches()

    # ---- regels -----------------------------------------------------------
    def _browse(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Kies map", edit.text() or os.path.expanduser("~"))
        if d:
            edit.setText(d)

    def load_rules_file(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Regels laden", "", "YAML (*.yaml *.yml)")
        if p:
            self.rules.setPlainText(open(p, encoding="utf-8").read())

    def save_rules_file(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Regels opslaan", "ordenen.yaml", "YAML (*.yaml)")
        if p:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(self.rules.toPlainText())

    # ---- voorbeeld --------------------------------------------------------
    def make_plan(self) -> None:
        src, dst = self.src.text().strip(), self.dst.text().strip()
        if not os.path.isdir(src) or not dst:
            warn(self, "Mappen kiezen", "Kies een bestaande bronmap en een doelmap.")
            return
        if os.path.realpath(dst).startswith(os.path.realpath(src) + os.sep):
            warn(self, "Doelmap", "De doelmap mag niet in de bronmap liggen.")
            return
        try:
            rules = R.load_rules(self.rules.toPlainText())
        except R.RuleError as exc:
            warn(self, "Regels kloppen niet", str(exc))
            return
        mode, conflict = self.mode.currentData(), self.conflict.currentData()

        def job(control, progress):
            sc = scan_roots([src], ScanOptions(), control, progress)
            return build_plan(sc.files, rules, dst, mode, conflict, control, progress)

        self.worker = Worker(job, self)
        self.worker.progress.connect(lambda s, d, t, det: (self.bar.setRange(0, t or 0), self.bar.setValue(d), self.summary.setText(stage_text(s, d, t, det))))
        self.worker.result.connect(self._plan_ready)
        self.worker.failed.connect(lambda m: (self._end(), warn(self, "Voorbeeld mislukt", m)))
        self.b_plan.setEnabled(False)
        self.b_go.setEnabled(False)
        self.worker.start()

    def _end(self) -> None:
        self.worker = None
        self.b_plan.setEnabled(True)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)

    def _plan_ready(self, plan: Plan) -> None:
        self._end()
        self.plan = plan
        self._fill_tree()
        self.warnings.setText("\n".join("! " + w for w in plan.warnings))

    def _fill_tree(self) -> None:
        plan = self.plan
        assert plan is not None
        self._building = True
        self.tree.clear()
        folders: dict[str, QTreeWidgetItem] = {}
        rest = QTreeWidgetItem(self.tree, ["Niet verplaatst (dubbel, al op plek, overgeslagen)"])
        for it in sorted(plan.items, key=lambda i: i.dst):
            if it.status != "ok":
                c = QTreeWidgetItem(rest, [os.path.basename(it.src), "", it.rule, it.note or it.status])
                c.setToolTip(0, it.src)
                continue
            rel = os.path.relpath(it.dest_dir, plan.dest_root)
            parent = folders.get(rel)
            if parent is None:
                parent = QTreeWidgetItem(self.tree, [rel + "/"])
                parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
                parent.setCheckState(0, Qt.CheckState.Checked)
                folders[rel] = parent
            c = QTreeWidgetItem(parent, [f"{os.path.basename(it.src)}  →  {os.path.basename(it.dst)}", human_size(it.size), it.rule, f"{it.date} ({it.date_source})" + (f" · {it.note}" if it.note else "")])
            c.setToolTip(0, f"{it.src}\n→ {it.dst}")
            c.setFlags(c.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            c.setCheckState(0, Qt.CheckState.Checked if it.include else Qt.CheckState.Unchecked)
            c.setData(0, Qt.ItemDataRole.UserRole, it)
        for rel, p in folders.items():
            p.setText(1, f"{p.childCount()} bestanden")
        self.tree.expandToDepth(0)
        self._building = False
        self._summary()

    def _changed(self, it: QTreeWidgetItem, col: int) -> None:
        if self._building or col != 0:
            return
        data = it.data(0, Qt.ItemDataRole.UserRole)
        if data is not None:
            data.include = it.checkState(0) == Qt.CheckState.Checked
        self._summary()

    def _summary(self) -> None:
        if not self.plan:
            return
        act = self.plan.active()
        s = self.plan.summary()
        verb = "kopiëren" if self.plan.mode == "copy" else "verplaatsen"
        self.summary.setText(f"{len(act)} bestanden ({human_size(sum(i.size for i in act))}) te {verb} · {s['duplicate']} al aanwezig · {s['already']} al op plek · {s['skip']} overgeslagen")
        self.b_go.setEnabled(bool(act) and not any("alleen-lezen" in w for w in self.plan.warnings))

    # ---- uitvoeren / ongedaan ----------------------------------------------
    def execute(self) -> None:
        plan = self.plan
        if not plan or not plan.active():
            return
        act = plan.active()
        verb = "gekopieerd" if plan.mode == "copy" else "verplaatst"
        if not confirm(self, "Ordenen uitvoeren",
                       f"{len(act)} bestanden worden {verb} naar:\n{plan.dest_root}\n\nJe kunt dit daarna ongedaan maken.", "Uitvoeren"):
            return
        self.worker = Worker(lambda control, progress: executor.execute(plan, self.cache, control, progress), self)
        self.worker.progress.connect(lambda s, d, t, det: (self.bar.setRange(0, t or 0), self.bar.setValue(d), self.summary.setText(stage_text(s, d, t, det))))
        self.worker.result.connect(self._executed)
        self.worker.failed.connect(lambda m: (self._end(), warn(self, "Ordenen mislukt", m)))
        self.b_plan.setEnabled(False)
        self.b_go.setEnabled(False)
        self.worker.start()

    def _executed(self, res: executor.ExecResult) -> None:
        self._end()
        self.plan = None
        self.tree.clear()
        msg = f"{res.done} bestanden verwerkt. Batch: {res.batch_id}"
        if res.failed:
            msg += f"\n{len(res.failed)} mislukt: " + "; ".join(f"{os.path.basename(p)} ({why})" for p, why in res.failed[:3])
        self.summary.setText(msg)
        self.refresh_batches()
        self.quarantineChanged.emit()

    def refresh_batches(self) -> None:
        self.undo_box.clear()
        import time
        for b in self.cache.journal_batches():
            if b["active"]:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b["ts"]))
                self.undo_box.addItem(f"{when} — {b['active']} acties ({b['batch_id']})", b["batch_id"])
        self.b_undo.setEnabled(self.undo_box.count() > 0)

    def undo(self) -> None:
        bid = self.undo_box.currentData()
        if not bid:
            return
        if not confirm(self, "Ongedaan maken", "Alle bestanden van deze batch gaan terug naar hun oorspronkelijke plek.", "Ongedaan maken"):
            return
        n, failed = executor.undo(bid, self.cache)
        msg = f"{n} acties ongedaan gemaakt."
        if failed:
            msg += "\nMislukt: " + "; ".join(f"{os.path.basename(p)} ({why})" for p, why in failed[:3])
        info(self, "Ongedaan maken", msg)
        self.refresh_batches()
        self.quarantineChanged.emit()
