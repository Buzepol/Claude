"""Voorbeeldpaneel: foto's naast elkaar met gesynchroniseerde zoom, EXIF en verschil-overlay."""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from typing import Optional

from PIL import Image, ImageChops
from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QPixmap, QTransform
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core.imageio import is_image, load_preview, read_meta
from ..core.models import FileInfo, human_size
from .common import pil_to_pixmap


class ImageView(QGraphicsView):
    """Zoom/pan; toestand uitgedrukt relatief (zoom t.o.v. 'passend', middelpunt als fractie)."""

    viewChanged = Signal(float, float, float)

    def __init__(self) -> None:
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: Optional[QGraphicsPixmapItem] = None
        self._fit = True
        self._zoom = 1.0
        self._blocked = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(self.renderHints())
        self.setMinimumSize(220, 180)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(self.palette().window())
        for bar in (self.horizontalScrollBar(), self.verticalScrollBar()):
            bar.valueChanged.connect(self._emit_state)

    def set_pixmap(self, pm: QPixmap) -> None:
        self._scene.clear()
        self._item = self._scene.addPixmap(pm)
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.setSceneRect(self._item.boundingRect())
        self.fit()

    def _base_scale(self) -> float:
        if not self._item:
            return 1.0
        r = self._item.boundingRect()
        vw, vh = max(1, self.viewport().width()), max(1, self.viewport().height())
        return min(vw / r.width(), vh / r.height())

    def fit(self) -> None:
        self._fit = True
        self._zoom = 1.0
        if self._item:
            s = self._base_scale()
            self.setTransform(QTransform.fromScale(s, s))
            self.centerOn(self._item)
        self._emit_state()

    def _emit_state(self) -> None:
        if self._blocked or not self._item:
            return
        r = self._item.boundingRect()
        c = self.mapToScene(self.viewport().rect().center())
        self.viewChanged.emit(self._zoom, c.x() / r.width(), c.y() / r.height())

    def apply_state(self, zoom: float, cx: float, cy: float) -> None:
        if not self._item:
            return
        self._blocked = True
        try:
            self._zoom = zoom
            self._fit = zoom == 1.0
            s = self._base_scale() * zoom
            self.setTransform(QTransform.fromScale(s, s))
            r = self._item.boundingRect()
            self.centerOn(QPointF(cx * r.width(), cy * r.height()))
        finally:
            self._blocked = False

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self._item:
            return
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self._zoom = max(1.0, min(40.0, self._zoom * factor))
        self._fit = self._zoom == 1.0
        s = self._base_scale() * self._zoom
        self.setTransform(QTransform.fromScale(s, s))
        self._emit_state()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.fit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._item:
            s = self._base_scale() * self._zoom
            self.setTransform(QTransform.fromScale(s, s))


class _Card(QFrame):
    def __init__(self, title: str, badge: str, badge_color: str, meta: str, view: Optional[ImageView], note: str = "") -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        head = QLabel(f"<b>{title}</b>" + (f" &nbsp;<span style='color:{badge_color}'>{badge}</span>" if badge else ""))
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)
        lay.addWidget(head)
        if view is not None:
            lay.addWidget(view, 1)
        else:
            ph = QLabel(note or "Geen voorbeeld beschikbaar")
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setMinimumHeight(120)
            lay.addWidget(ph, 1)
        m = QLabel(meta)
        m.setWordWrap(True)
        m.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        m.setStyleSheet("color: palette(mid);")
        lay.addWidget(m)
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


class ComparePanel(QWidget):
    """Toont tot MAX_CARDS bestanden naast elkaar."""

    MAX_CARDS = 4

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.diff_box = QCheckBox("Toon verschil (eerste twee foto's)")
        self.diff_box.toggled.connect(lambda _=None: self.refresh())
        self.hint = QLabel("Scroll = zoom (gelijk in alle foto's) · dubbelklik = passend")
        self.hint.setStyleSheet("color: palette(mid);")
        bar.addWidget(self.diff_box)
        bar.addStretch(1)
        bar.addWidget(self.hint)
        outer.addLayout(bar)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.row = QHBoxLayout(self.body)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.area.setWidget(self.body)
        outer.addWidget(self.area, 1)
        self._files: list[FileInfo] = []
        self._marks: dict[str, str] = {}
        self._views: list[ImageView] = []
        self._syncing = False
        self._cache: "OrderedDict[str, Image.Image]" = OrderedDict()

    # ---- publieke API ---------------------------------------------------
    def show_files(self, files: list[FileInfo], marks: Optional[dict[str, str]] = None, first: Optional[str] = None) -> None:
        """marks: pad -> 'keep' | 'delete' | tekst-label. first: pad dat vooraan komt."""
        files = list(files)
        if first:
            files.sort(key=lambda f: f.path != first)
        self._files = files[: self.MAX_CARDS]
        self._marks = marks or {}
        self.refresh()

    def clear(self) -> None:
        self._files = []
        self.refresh()

    # ---- intern -----------------------------------------------------------
    def _image(self, path: str) -> Image.Image:
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]
        im = load_preview(path, 1600)
        self._cache[path] = im
        while len(self._cache) > 12:
            self._cache.popitem(last=False)
        return im

    def refresh(self) -> None:
        while self.row.count():
            item = self.row.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._views = []
        images: list[Image.Image] = []
        for f in self._files:
            mark = self._marks.get(f.path, "")
            badge, color = {"keep": ("BEHOUDEN", "#2e8b57"), "delete": ("NAAR QUARANTAINE", "#c0392b")}.get(mark, (mark, "#888"))
            meta_lines = [human_size(f.size), time.strftime("gewijzigd %Y-%m-%d %H:%M", time.localtime(f.mtime_ns / 1e9))]
            view = None
            note = ""
            if is_image(f.ext):
                try:
                    im = self._image(f.path)
                    m = read_meta(f.path)
                    meta_lines.insert(1, f"{m.width}×{m.height} px")
                    if m.taken:
                        meta_lines.append(f"opname {m.taken}")
                    if m.camera:
                        meta_lines.append(m.camera)
                    view = ImageView()
                    view.set_pixmap(pil_to_pixmap(im))
                    view.viewChanged.connect(self._sync)
                    self._views.append(view)
                    images.append(im)
                except Exception as exc:
                    note = f"Kan niet openen: {exc}"
            meta_lines.append(f.path)
            self.row.addWidget(_Card(f.name, badge, color, "\n".join(meta_lines), view, note))
        if self.diff_box.isChecked() and len(images) >= 2:
            a = images[0]
            b = images[1].resize(a.size)
            d = ImageChops.difference(a, b).point(lambda p: min(255, p * 4))
            view = ImageView()
            view.set_pixmap(pil_to_pixmap(d))
            view.viewChanged.connect(self._sync)
            self._views.append(view)
            self.row.addWidget(_Card("Verschil (×4)", "", "#888", "Donker = gelijk, licht = verschil", view))
        self.row.addStretch(0)

    def _sync(self, zoom: float, cx: float, cy: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            for v in self._views:
                if v is not self.sender():
                    v.apply_state(zoom, cx, cy)
        finally:
            self._syncing = False
