"""Tab 1: bronnen kiezen, detectie-opties, scan starten/pauzeren/stoppen."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMenu, QProgressBar,
    QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from ..core.db import Cache
from ..core.detectors.media import tools_available
from ..core.drives import list_external_volumes, volume_for_path
from ..core.engine import DetectOptions, DetectResult, run_detection
from ..core.scanner import ScanOptions
from .common import Worker, stage_text, warn


class SourcesTab(QWidget):
    scanFinished = Signal(object)  # DetectResult

    def __init__(self, cache: Cache) -> None:
        super().__init__()
        self.cache = cache
        self.worker: Worker | None = None
        lay = QVBoxLayout(self)

        box = QGroupBox("Bronnen (mappen of hele schijven)")
        bl = QVBoxLayout(box)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        bl.addWidget(self.list)
        row = QHBoxLayout()
        b_add = QPushButton("Map toevoegen…")
        b_add.clicked.connect(self.add_folder)
        self.b_vol = QPushButton("Schijf toevoegen ▾")
        self.b_vol.clicked.connect(self.add_volume_menu)
        b_rm = QPushButton("Verwijderen")
        b_rm.clicked.connect(self.remove_selected)
        for b in (b_add, self.b_vol, b_rm):
            row.addWidget(b)
        row.addStretch(1)
        bl.addLayout(row)
        self.vol_info = QLabel("")
        self.vol_info.setWordWrap(True)
        bl.addWidget(self.vol_info)
        lay.addWidget(box)

        opt = QGroupBox("Wat zoeken?")
        ol = QVBoxLayout(opt)
        self.c_exact = QCheckBox("Exact identiek (aanbevolen, 100% betrouwbaar)")
        self.c_exact.setChecked(True)
        self.c_img = QCheckBox("Gelijkende foto's (andere resolutie/compressie)")
        self.c_img.setChecked(True)
        self.c_name = QCheckBox("Zelfde naam + grootte (onzeker, alleen als ‘mogelijk’)")
        tools = tools_available()
        self.c_media = QCheckBox("Gelijkende video/audio (ffmpeg" + ("" if tools["ffmpeg"] else " niet gevonden") + ")")
        self.c_media.setEnabled(tools["ffmpeg"] and tools["ffprobe"])
        for c in (self.c_exact, self.c_img, self.c_name, self.c_media):
            ol.addWidget(c)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Foto-gelijkenis:"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 12)
        self.slider.setValue(5)
        self.slider_lbl = QLabel()
        self.slider.valueChanged.connect(self._slider_text)
        self._slider_text(5)
        trow.addWidget(QLabel("streng"))
        trow.addWidget(self.slider, 1)
        trow.addWidget(QLabel("ruim"))
        trow.addWidget(self.slider_lbl)
        ol.addLayout(trow)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Minimale grootte (KB):"))
        self.min_kb = QSpinBox()
        self.min_kb.setRange(0, 10_000_000)
        self.min_kb.setValue(1)
        r2.addWidget(self.min_kb)
        r2.addWidget(QLabel("Uitsluiten (patronen, komma-gescheiden):"))
        self.excl = QLineEdit()
        self.excl.setPlaceholderText("bv. *.tmp, node_modules")
        r2.addWidget(self.excl, 1)
        ol.addLayout(r2)
        note = QLabel("Apple Foto's-bibliotheken (*.photoslibrary) worden altijd overgeslagen. Er worden geen bestanden gewijzigd tijdens het scannen.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        ol.addWidget(note)
        if sys.platform == "darwin":
            tip = QLabel("Tip: geef DupeScout ‘Volledige schijftoegang’ (Systeeminstellingen → Privacy en beveiliging) om alle mappen en externe schijven te kunnen lezen.")
            tip.setWordWrap(True)
            tip.setStyleSheet("color: palette(mid);")
            ol.addWidget(tip)
        lay.addWidget(opt)

        ctl = QHBoxLayout()
        self.b_start = QPushButton("Scan starten")
        self.b_start.clicked.connect(self.start)
        self.b_pause = QPushButton("Pauzeren")
        self.b_pause.setEnabled(False)
        self.b_pause.clicked.connect(self.toggle_pause)
        self.b_stop = QPushButton("Stoppen")
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self.stop)
        for b in (self.b_start, self.b_pause, self.b_stop):
            ctl.addWidget(b)
        ctl.addStretch(1)
        lay.addLayout(ctl)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        lay.addWidget(self.bar)
        self.status = QLabel("Klaar om te scannen.")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self.list.itemSelectionChanged.connect(self.update_vol_info)

    # ---- bronnen ----------------------------------------------------------
    def roots(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def add_path(self, path: str) -> None:
        path = os.path.realpath(path)
        if path not in self.roots():
            self.list.addItem(path)
        self.update_vol_info()

    def add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Kies een map of schijf", "/Volumes" if os.path.isdir("/Volumes") else os.path.expanduser("~"))
        if d:
            self.add_path(d)

    def add_volume_menu(self) -> None:
        menu = QMenu(self)
        vols = list_external_volumes()
        if not vols:
            menu.addAction("Geen externe schijven gevonden").setEnabled(False)
        for v in vols:
            menu.addAction(v, lambda v=v: self.add_path(v))
        menu.exec(self.b_vol.mapToGlobal(self.b_vol.rect().bottomLeft()))

    def remove_selected(self) -> None:
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self.update_vol_info()

    def update_vol_info(self) -> None:
        lines = []
        for r in self.roots():
            try:
                v = volume_for_path(r)
            except OSError:
                continue
            line = f"{r}: schijf ‘{v.name}’, {v.fs_type}"
            if not v.writable:
                line += " — alleen-lezen: scannen en vergelijken kan, quarantaine en ordenen niet"
            lines.append(line)
        self.vol_info.setText("\n".join(lines))

    # ---- scan -------------------------------------------------------------
    def _slider_text(self, v: int) -> None:
        self.slider_lbl.setText(f"{v} (0 = identiek beeld)")

    def options(self) -> DetectOptions:
        excl = [p.strip() for p in self.excl.text().split(",") if p.strip()]
        return DetectOptions(
            exact=self.c_exact.isChecked(),
            similar_images=self.c_img.isChecked(),
            name_size=self.c_name.isChecked(),
            similar_media=self.c_media.isChecked() and self.c_media.isEnabled(),
            image_threshold=self.slider.value(),
            scan=ScanOptions(excludes=excl, min_size=max(1, self.min_kb.value() * 1024) if self.min_kb.value() > 1 else 1),
        )

    def start(self) -> None:
        if not self.roots():
            warn(self, "Geen bronnen", "Voeg eerst minstens één map of schijf toe.")
            return
        opts = self.options()
        if not (opts.exact or opts.similar_images or opts.name_size or opts.similar_media):
            warn(self, "Niets gekozen", "Kies minstens één zoekmethode.")
            return
        roots = self.roots()
        self.worker = Worker(lambda control, progress: run_detection(roots, opts, self.cache, control, progress), self)
        self.worker.progress.connect(lambda s, d, t, det: (self._set_bar(d, t), self.status.setText(stage_text(s, d, t, det))))
        self.worker.result.connect(self._done)
        self.worker.failed.connect(lambda m: (self._reset(), warn(self, "Scan mislukt", m)))
        self.worker.cancelled.connect(lambda: (self._reset(), self.status.setText("Scan gestopt.")))
        self.b_start.setEnabled(False)
        self.b_pause.setEnabled(True)
        self.b_stop.setEnabled(True)
        self.status.setText("Bezig…")
        self.worker.start()

    def _set_bar(self, done: int, total: int) -> None:
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        else:
            self.bar.setRange(0, 0)

    def _reset(self) -> None:
        self.b_start.setEnabled(True)
        self.b_pause.setEnabled(False)
        self.b_pause.setText("Pauzeren")
        self.b_stop.setEnabled(False)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)

    def toggle_pause(self) -> None:
        if not self.worker:
            return
        if self.worker.control.paused:
            self.worker.control.resume()
            self.b_pause.setText("Pauzeren")
        else:
            self.worker.control.pause()
            self.b_pause.setText("Hervatten")

    def stop(self) -> None:
        if self.worker:
            self.worker.control.cancel()

    def _done(self, res: DetectResult) -> None:
        self._reset()
        from ..core.models import human_size
        msg = f"{len(res.files):,} bestanden gescand, {len(res.groups):,} groepen gevonden".replace(",", ".")
        if res.reclaimable_exact:
            msg += f" — tot {human_size(res.reclaimable_exact)} terug te winnen (exacte duplicaten)"
        if res.skipped_libraries:
            msg += f"\n{len(res.skipped_libraries)} Apple Foto's-bibliotheek(en) overgeslagen."
        if res.errors:
            msg += f"\n{len(res.errors)} bestanden/mappen konden niet gelezen worden (bijv. geen toegang)."
        self.status.setText(msg)
        self.scanFinished.emit(res)
