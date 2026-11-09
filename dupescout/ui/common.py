"""Gedeelde UI-hulpmiddelen: achtergrondtaken, dialogen, pixmap-conversie."""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

from PIL import Image
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QMessageBox, QWidget

from ..core.jobs import Cancelled, JobControl


class Worker(QThread):
    """Voert fn(control, progress) uit op een achtergrondthread."""

    progress = Signal(str, int, int, str)
    result = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn: Callable[[JobControl, Callable[[str, int, int, str], None]], Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.fn = fn
        self.control = JobControl()
        self._last = 0.0

    def _emit(self, stage: str, done: int, total: int, detail: str) -> None:
        now = time.monotonic()
        if now - self._last > 0.1 or (total and done >= total):
            self._last = now
            self.progress.emit(stage, done, total, detail)

    def run(self) -> None:
        try:
            out = self.fn(self.control, self._emit)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:  # toon elke fout aan de gebruiker
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.result.emit(out)


STAGE_NAMES = {
    "scan": "Bestanden zoeken",
    "partial": "Eerste/laatste blok vergelijken",
    "full": "Volledige inhoud vergelijken",
    "images": "Foto's analyseren",
    "media": "Audio/video analyseren",
    "plan": "Voorstel maken",
    "organize": "Ordenen",
    "done": "Klaar",
}


def stage_text(stage: str, done: int, total: int, detail: str) -> str:
    name = STAGE_NAMES.get(stage, stage)
    counts = f"{done:,}/{total:,}" if total else f"{done:,}"
    return f"{name}: {counts}  {os.path.basename(detail)}".replace(",", ".")


def pil_to_pixmap(im: Image.Image) -> QPixmap:
    im = im.convert("RGB")
    data = im.tobytes("raw", "RGB")
    qi = QImage(data, im.width, im.height, 3 * im.width, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qi)


def confirm(parent: QWidget, title: str, text: str, ok_text: str = "Doorgaan", default_no: bool = True) -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=parent)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Annuleren", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel if default_no else ok)
    box.exec()
    return box.clickedButton() is ok


def warn(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def info(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)
