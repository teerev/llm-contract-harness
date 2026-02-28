"""LocalRunner — executes the pipeline in a background thread."""

from __future__ import annotations

import threading

from web.server.interfaces import RunOptions, RunStore
from web.server.pipeline import execute_pipeline


class LocalRunner:
    """Starts pipeline execution in a daemon thread.

    Only one pipeline runs at a time.  ``start()`` raises
    ``RuntimeError`` if a run is already in progress — the API layer
    should translate this into a 429.
    """

    def __init__(self, run_store: RunStore) -> None:
        self._run_store = run_store
        self._semaphore = threading.Semaphore(1)

    @property
    def busy(self) -> bool:
        """True if a pipeline is currently running."""
        acquired = self._semaphore.acquire(blocking=False)
        if acquired:
            self._semaphore.release()
            return False
        return True

    def start(self, run_id: str, prompt: str, opts: RunOptions) -> None:
        if not self._semaphore.acquire(blocking=False):
            raise RuntimeError("A pipeline is already running. Please wait for it to finish.")

        def _run() -> None:
            try:
                execute_pipeline(run_id, prompt, opts, self._run_store)
            finally:
                self._semaphore.release()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
