"""Append-only, per-run event log backed by a JSONL file.

Each line is a self-contained JSON object with at least::

    {"seq": 1, "ts": "2026-...", "type": "planner_chunk", ...}

The SSE endpoint tails this file.  In a future AWS deployment the
backing store can be swapped for DynamoDB / S3 without changing callers.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any


class EventLog:
    """Thread-safe, append-only event writer."""

    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        self._seq = 0
        self._fh = open(path, "a", encoding="utf-8")  # noqa: SIM115

    @property
    def path(self) -> str:
        return self._path

    def emit(self, event_type: str, **data: Any) -> dict:
        """Append one event and return it."""
        with self._lock:
            self._seq += 1
            event: dict[str, Any] = {
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "type": event_type,
                **data,
            }
            self._fh.write(json.dumps(event, separators=(",", ":")) + "\n")
            self._fh.flush()
            return event

    def close(self) -> None:
        with self._lock:
            self._fh.close()
