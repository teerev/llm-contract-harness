"""Pipeline orchestration — runs planner (and later factory) in-process.

Called from LocalRunner in a background thread.  Writes events to the
per-run EventLog and updates RunStore metadata at each stage transition.
"""

from __future__ import annotations

import os
import subprocess
import traceback
from datetime import datetime, timezone

from shared.event_log import EventLog
from web.server import config
from web.server.interfaces import RunOptions, RunStore


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def execute_pipeline(
    run_id: str,
    prompt: str,
    opts: RunOptions,
    run_store: RunStore,
) -> None:
    """Run the full pipeline: planner compile (→ factory in WP3).

    This function blocks — the caller is responsible for running it in a
    background thread.
    """
    run_dir = os.path.join(config.ARTIFACTS_DIR, "pipeline", run_id)
    events_path = run_store.events_path(run_id)
    log = EventLog(events_path)

    try:
        _run_planner(run_id, prompt, run_dir, log, run_store)
    except Exception as exc:
        run_store.update(run_id, status="failed", finished_at=_ts(), error=str(exc))
        log.emit("pipeline_status", status="failed", error=str(exc))
        log.emit("console", text=traceback.format_exc(), level="error")
        return
    finally:
        log.close()

    # TODO WP3: factory execution loop goes here


def _run_planner(
    run_id: str,
    prompt: str,
    run_dir: str,
    log: EventLog,
    run_store: RunStore,
) -> None:
    """Execute the planner stage and emit events."""
    from planner.compiler import compile_plan

    run_store.update(run_id, status="planning")
    log.emit("pipeline_status", status="planning")

    # Save the user prompt as a spec file
    spec_path = os.path.join(run_dir, "spec.txt")
    with open(spec_path, "w", encoding="utf-8") as fh:
        fh.write(prompt)

    # Create a fresh target repo for this run
    repo_dir = os.path.join(run_dir, "repo")
    _init_repo(repo_dir)

    result = compile_plan(
        spec_path=spec_path,
        artifacts_dir=config.ARTIFACTS_DIR,
        repo_path=repo_dir,
        event_log=log,
    )

    planner_run_id = result.run_id
    run_store.update(
        run_id,
        planner_run_id=planner_run_id,
        work_order_count=len(result.work_orders),
    )

    if not result.success:
        error_msg = "; ".join(result.errors) if result.errors else "planner compilation failed"
        run_store.update(run_id, status="failed", finished_at=_ts(), error=error_msg)
        log.emit("pipeline_status", status="failed", error=error_msg)
        return

    # Planner succeeded — mark pipeline complete for now (factory in WP3)
    run_store.update(run_id, status="complete", finished_at=_ts())
    log.emit("pipeline_status", status="complete")


def _init_repo(repo_dir: str) -> None:
    """Create a fresh git repo with one empty commit."""
    os.makedirs(repo_dir, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "llmch@local"],
        cwd=repo_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "llmch"],
        cwd=repo_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=repo_dir, check=True, capture_output=True,
    )
