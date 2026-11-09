"""Pauzeren, annuleren en voortgang voor langlopende taken."""

from __future__ import annotations

import threading
from typing import Callable, Optional

# progress(stage, done, total, detail)
ProgressFn = Callable[[str, int, int, str], None]


class Cancelled(Exception):
    """De gebruiker heeft de taak afgebroken."""


class JobControl:
    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._running = threading.Event()
        self._running.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._running.set()  # laat een gepauzeerde taak doorlopen zodat hij kan stoppen

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def checkpoint(self) -> None:
        """Roep regelmatig aan: wacht bij pauze en gooit Cancelled bij annuleren."""
        self._running.wait()
        if self._cancel.is_set():
            raise Cancelled()


def checkpoint(control: Optional[JobControl]) -> None:
    if control is not None:
        control.checkpoint()


def report(progress: Optional[ProgressFn], stage: str, done: int, total: int, detail: str = "") -> None:
    if progress is not None:
        progress(stage, done, total, detail)
