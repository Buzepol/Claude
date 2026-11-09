"""Hoofdvenster en startpunt van de app."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget

from .. import __version__
from ..core.db import Cache
from .compare_tab import CompareTab
from .organize_tab import OrganizeTab
from .quarantine_tab import QuarantineTab
from .results import ResultsTab
from .sources import SourcesTab


class MainWindow(QMainWindow):
    def __init__(self, cache: Cache | None = None) -> None:
        super().__init__()
        self.cache = cache or Cache()
        self.setWindowTitle(f"DupeScout {__version__}")
        self.resize(1280, 820)
        self.tabs = QTabWidget()
        self.sources = SourcesTab(self.cache)
        self.results = ResultsTab(self.cache)
        self.compare = CompareTab(self.cache)
        self.organize = OrganizeTab(self.cache)
        self.quarantine = QuarantineTab(self.cache)
        self.tabs.addTab(self.sources, "1 · Bronnen && scan")
        self.tabs.addTab(self.results, "2 · Resultaten")
        self.tabs.addTab(self.compare, "3 · Mappen vergelijken")
        self.tabs.addTab(self.organize, "4 · Ordenen")
        self.tabs.addTab(self.quarantine, "5 · Quarantaine && logboek")
        self.setCentralWidget(self.tabs)
        self.sources.scanFinished.connect(self._scan_done)
        for t in (self.results, self.compare, self.organize):
            t.quarantineChanged.connect(self.quarantine.refresh)
        self.tabs.currentChanged.connect(lambda i: self.quarantine.refresh() if i == 4 else None)

    def _scan_done(self, res) -> None:
        self.results.set_result(res)
        self.tabs.setCurrentWidget(self.results)

    def closeEvent(self, e) -> None:  # noqa: N802
        self.cache.close()
        super().closeEvent(e)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DupeScout")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
