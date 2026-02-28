"""SSE (Server-Sent Events) helpers for streaming events.jsonl to the browser.

The generator reads the append-only events file, yields existing lines,
then tails for new lines until a terminal event or client disconnect.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator

TAIL_POLL_INTERVAL = 0.25  # seconds between file-tail polls
KEEPALIVE_INTERVAL = 15.0  # seconds between ping events
TERMINAL_TYPES = {"pipeline_status"}
TERMINAL_STATUSES = {"complete", "failed"}


async def stream_events(
    events_path: str,
    last_seq: int = 0,
) -> AsyncGenerator[str, None]:
    """Yield SSE-formatted lines from an events.jsonl file.

    Replays events with seq > *last_seq*, then tails for new lines.
    Emits ``event: ping`` keepalives.  Stops after a terminal event
    (pipeline_status with status complete/failed).
    """
    for _ in range(40):
        if os.path.isfile(events_path):
            break
        await asyncio.sleep(TAIL_POLL_INTERVAL)
    else:
        yield _sse("error", {"type": "error", "error": "events file not found"})
        return

    offset = 0
    since_keepalive = 0.0

    while True:
        lines_read = 0
        try:
            with open(events_path, "r", encoding="utf-8") as fh:
                fh.seek(offset)
                while True:
                    raw_line = fh.readline()
                    if not raw_line:
                        offset = fh.tell()
                        break
                    offset = fh.tell()
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        event = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    seq = event.get("seq", 0)
                    if seq <= last_seq:
                        continue

                    etype = event.get("type", "message")
                    yield _sse(etype, event)
                    lines_read += 1
                    since_keepalive = 0.0

                    if _is_terminal(event):
                        yield _sse("done", event)
                        return
        except FileNotFoundError:
            pass

        await asyncio.sleep(0)

        if lines_read == 0:
            await asyncio.sleep(TAIL_POLL_INTERVAL)
            since_keepalive += TAIL_POLL_INTERVAL
            if since_keepalive >= KEEPALIVE_INTERVAL:
                yield _sse("ping", {"type": "ping"})
                since_keepalive = 0.0


def _sse(event_name: str, data: dict) -> str:
    """Format a single SSE frame."""
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event_name}\ndata: {payload}\n\n"


def _is_terminal(event: dict) -> bool:
    return (
        event.get("type") in TERMINAL_TYPES
        and event.get("status") in TERMINAL_STATUSES
    )
