"""The operator's stop, as a batch command sees it.

A supervisor stops a process with ``SIGTERM`` (``timeout``, systemd, cron),
a person with Ctrl-C; on Windows a parent process sends ``CTRL_BREAK_EVENT``.
A command that honours none of them ends where it stands, with no report. One
that honours only Ctrl-C is killed by its own scheduler.

:class:`Interrupt` turns the first of these signals into a flag the command
reads at its own boundaries — between the items of a sync, the files of a
recursive add — so it finishes the item it is on, keeps what it committed, and
reports ``cancelled`` with exit 130. The second signal ends the process at
once, with the same exit code and no report: an operator who sends it twice
has decided not to wait, and the catalog is safe to leave because every item is
its own transaction.
"""

from __future__ import annotations

import os
import signal
import sys

CANCELLED_EXIT = 130


def stop_signals() -> tuple[int, ...]:
    """The signals that mean "stop" on this platform."""
    signals = [signal.SIGINT, signal.SIGTERM]
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signals.append(sigbreak)
    return tuple(signals)


class Interrupt:
    """Install with :meth:`install`; ask :attr:`requested` between items."""

    def __init__(self) -> None:
        self.requested = False
        self._previous: dict[int, object] = {}

    def install(self) -> "Interrupt":
        for number in stop_signals():
            try:
                self._previous[number] = signal.signal(number, self._handle)
            except (ValueError, OSError):
                # Not the main thread, or a signal this platform will not let
                # us take: the process then stops the way it always did.
                continue
        return self

    def _handle(self, number: int, _frame: object) -> None:
        if self.requested:
            sys.stderr.write("Interrupted again; stopping now.\n")
            sys.stderr.flush()
            os._exit(CANCELLED_EXIT)
        self.requested = True

    def is_cancelled(self) -> bool:
        return self.requested

    def restore(self) -> None:
        for number, previous in self._previous.items():
            try:
                signal.signal(number, previous)  # type: ignore[arg-type]
            except (ValueError, OSError):
                continue
        self._previous.clear()
