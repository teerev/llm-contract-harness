"""Fake runner that writes canned SSE events on a timer for UI development."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from shared.event_log import EventLog
from web.server.interfaces import RunOptions, RunStore


_FAKE_WOS = [
    {"id": "WO-01", "title": "Bootstrap verify script"},
    {"id": "WO-02", "title": "Create project scaffold"},
    {"id": "WO-03", "title": "Implement core logic"},
    {"id": "WO-04", "title": "Add CLI entry point"},
    {"id": "WO-05", "title": "Write tests"},
]


class FakeRunner:
    """Writes a realistic sequence of events to events.jsonl without calling any LLMs."""

    def __init__(self, run_store: RunStore) -> None:
        self._run_store = run_store

    def start(self, run_id: str, prompt: str, opts: RunOptions) -> None:
        t = threading.Thread(target=self._run, args=(run_id,), daemon=True)
        t.start()

    def _run(self, run_id: str) -> None:
        log = EventLog(self._run_store.events_path(run_id))
        try:
            self._simulate(run_id, log)
        finally:
            log.close()

    def _simulate(self, run_id: str, log: EventLog) -> None:
        _ts = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # noqa: E731

        self._run_store.update(run_id, status="planning")
        log.emit("pipeline_status", status="planning")
        time.sleep(0.3)

        # Planner reasoning chunks
        log.emit("planner_reasoning_status", status="start")
        reasoning = [
            "Analyzing the product specification...\n",
            "The user wants a command-line application.\n",
            "I need to decompose this into work orders:\n",
            "1. Bootstrap the verify script\n",
            "2. Create the project scaffold with __init__.py\n",
            "3. Implement the core game logic\n",
            "4. Add a CLI entry point via __main__.py\n",
            "5. Write comprehensive tests\n",
        ]
        for chunk in reasoning:
            log.emit("planner_chunk", text=chunk)
            time.sleep(0.3)
        log.emit("planner_reasoning_status", status="end")

        log.emit("planner_status", status="attempt_start", attempt=1, max_attempts=5)
        time.sleep(0.5)
        log.emit("planner_status", status="attempt_pass", attempt=1, max_attempts=5)

        planner_run_id = f"FAKE_PLANNER_{run_id[:12]}"
        self._run_store.update(
            run_id,
            planner_run_id=planner_run_id,
            work_order_count=len(_FAKE_WOS),
        )

        log.emit(
            "work_orders_created",
            count=len(_FAKE_WOS),
            work_orders=[{"id": wo["id"], "title": wo["title"]} for wo in _FAKE_WOS],
        )
        log.emit("planner_status", status="done")
        time.sleep(0.3)

        # Factory per-WO
        self._run_store.update(run_id, status="building")
        log.emit("pipeline_status", status="building")

        verdicts: dict[str, str] = {}
        factory_ids: list[str] = []
        for wo in _FAKE_WOS:
            wo_id = wo["id"]
            fid = f"FAKE_FACTORY_{wo_id}"
            factory_ids.append(fid)

            log.emit("wo_status", wo_id=wo_id, status="running", factory_run_id=fid)
            time.sleep(0.3)

            log.emit("wo_status", wo_id=wo_id, status="attempt_1", attempt=1)
            time.sleep(0.3)

            log.emit(
                "file_written",
                wo_id=wo_id,
                files=[{"path": f"src/{wo_id.lower().replace('-','_')}.py", "line_count": 42}],
            )
            time.sleep(0.2)

            log.emit("wo_status", wo_id=wo_id, status="pass")
            verdicts[wo_id] = "pass"
            self._run_store.update(run_id, work_order_verdicts=verdicts, factory_run_ids=factory_ids)
            time.sleep(0.2)

        self._run_store.update(
            run_id,
            status="complete",
            finished_at=_ts(),
        )
        log.emit("pipeline_status", status="complete")
