"""Background workers.

Every long operation runs off the UI thread with progress and a working cancel
button. Nothing blocks the event loop.

Callbacks are routed through a dispatcher that lives in the GUI thread. Connecting
a plain Python function to a signal gives Qt no receiver context, so it fires as a
direct connection and the callback ends up executing in the worker thread. Any
widget touched there is a race at best and a crash at worst, so the dispatcher
gives Qt a QObject in the right thread and the connection becomes queued.
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QThread, Signal, Slot


class Worker(QObject):
    progress = Signal(int, int)      # current, total (total is -1 when unknown)
    message = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @Slot()
    def run(self) -> None:
        def report(current, total=-1):
            if self._cancelled:
                raise InterruptedError("cancelled")
            self.progress.emit(int(current), int(total if total is not None else -1))

        try:
            result = self._fn(*self._args, progress=report, **self._kwargs)
        except InterruptedError:
            self.failed.emit("cancelled")
        except Exception:
            self.failed.emit(traceback.format_exc())
        else:
            self.finished.emit(result)


class Dispatcher(QObject):
    """Receives worker signals in the GUI thread and forwards them to callables."""

    def __init__(self, parent=None, on_done=None, on_fail=None,
                 on_progress=None, on_message=None):
        super().__init__(parent)
        self._on_done = on_done
        self._on_fail = on_fail
        self._on_progress = on_progress
        self._on_message = on_message

    @Slot(object)
    def done(self, payload) -> None:
        if self._on_done:
            self._on_done(payload)

    @Slot(str)
    def fail(self, message: str) -> None:
        if self._on_fail:
            self._on_fail(message)

    @Slot(int, int)
    def progressed(self, current: int, total: int) -> None:
        if self._on_progress:
            self._on_progress(current, total)

    @Slot(str)
    def messaged(self, text: str) -> None:
        if self._on_message:
            self._on_message(text)


def start(parent, fn, *args, on_done=None, on_fail=None, on_progress=None,
          on_message=None, **kwargs) -> tuple[QThread, Worker]:
    """Run ``fn`` on a QThread, delivering callbacks in ``parent``'s thread.

    Keep the returned pair alive for the duration of the work.
    """
    thread = QThread(parent)
    worker = Worker(fn, *args, **kwargs)
    worker.moveToThread(thread)

    dispatcher = Dispatcher(parent, on_done, on_fail, on_progress, on_message)
    worker._dispatcher = dispatcher  # keep it alive alongside the worker

    thread.started.connect(worker.run)
    worker.finished.connect(dispatcher.done)
    worker.failed.connect(dispatcher.fail)
    worker.progress.connect(dispatcher.progressed)
    worker.message.connect(dispatcher.messaged)

    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.start()
    return thread, worker
