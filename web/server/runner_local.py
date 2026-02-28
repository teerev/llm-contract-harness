"""LocalRunner — executes the pipeline in a background thread."""

from __future__ import annotations

import threading

from web.server.interfaces import RunOptions, RunStore
from web.server.pipeline import execute_pipeline


class LocalRunner:
    """Starts pipeline execution in a daemon thread.

    The thread calls ``execute_pipeline()`` which writes events to
    ``events.jsonl`` and updates ``RunStore`` metadata as it progresses.
    """

    def __init__(self, run_store: RunStore) -> None:
        self._run_store = run_store

    def start(self, run_id: str, prompt: str, opts: RunOptions) -> None:
        t = threading.Thread(
            target=execute_pipeline,
            args=(run_id, prompt, opts, self._run_store),
            daemon=True,
        )
        t.start()
