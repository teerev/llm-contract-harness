"""Pipeline orchestration — runs planner then factory in-process.

Called from LocalRunner in a background thread.  Writes events to the
per-run EventLog and updates RunStore metadata at each stage transition.
"""

from __future__ import annotations

import glob
import json
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
    """Run the full pipeline: planner compile → factory run per WO.

    This function blocks — the caller is responsible for running it in a
    background thread.
    """
    run_dir = os.path.join(config.ARTIFACTS_DIR, "pipeline", run_id)
    events_path = run_store.events_path(run_id)
    log = EventLog(events_path)

    try:
        wo_files = _run_planner(run_id, prompt, run_dir, log, run_store)
        if wo_files:
            _run_factory(run_id, run_dir, wo_files, log, run_store)
    except Exception as exc:
        run_store.update(run_id, status="failed", finished_at=_ts(), error=str(exc))
        log.emit("pipeline_status", status="failed", error=str(exc))
        log.emit("console", text=traceback.format_exc(), level="error")
    finally:
        log.close()


# ---------------------------------------------------------------------------
# Planner stage
# ---------------------------------------------------------------------------

def _run_planner(
    run_id: str,
    prompt: str,
    run_dir: str,
    log: EventLog,
    run_store: RunStore,
) -> list[str]:
    """Execute the planner stage. Returns list of WO file paths, or [] on failure."""
    from planner.compiler import compile_plan

    run_store.update(run_id, status="planning")
    log.emit("pipeline_status", status="planning")

    spec_path = os.path.join(run_dir, "spec.txt")
    with open(spec_path, "w", encoding="utf-8") as fh:
        fh.write(prompt)

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
        return []

    # Discover WO files from the canonical planner output
    output_dir = os.path.join(config.ARTIFACTS_DIR, "planner", planner_run_id, "output")
    wo_files = sorted(glob.glob(os.path.join(output_dir, "WO-*.json")))
    return wo_files


# ---------------------------------------------------------------------------
# Factory stage
# ---------------------------------------------------------------------------

def _run_factory(
    run_id: str,
    run_dir: str,
    wo_files: list[str],
    log: EventLog,
    run_store: RunStore,
) -> None:
    """Execute factory for each WO sequentially."""
    from factory.run import run_work_order
    from factory.runtime import ensure_repo_venv, venv_env
    from factory.util import _sandboxed_env

    run_store.update(run_id, status="building")
    log.emit("pipeline_status", status="building")

    repo_dir = os.path.join(run_dir, "repo")
    branch = f"factory/pipeline-{run_id}"

    # Set up the target-repo venv once for all WOs
    venv_root = ensure_repo_venv(repo_dir)
    command_env = venv_env(venv_root, _sandboxed_env())

    # Queue all WOs as pending
    for wo_path in wo_files:
        with open(wo_path, "r", encoding="utf-8") as fh:
            wo_data = json.load(fh)
        log.emit("wo_status", wo_id=wo_data.get("id", "?"), status="queued")

    verdicts: dict[str, str] = {}
    factory_run_ids: list[str] = []

    for i, wo_path in enumerate(wo_files):
        with open(wo_path, "r", encoding="utf-8") as fh:
            wo_data = json.load(fh)
        wo_id = wo_data.get("id", f"WO-{i+1:02d}")

        log.emit("wo_status", wo_id=wo_id, status="running")

        result = run_work_order(
            repo_root=repo_dir,
            work_order_path=wo_path,
            branch=branch,
            artifacts_dir=config.ARTIFACTS_DIR,
            command_env=command_env,
            is_first_wo=(i == 0),
            event_log=log,
        )

        factory_run_id = result.get("run_id", "")
        verdict = result.get("verdict", "ERROR")
        if factory_run_id:
            factory_run_ids.append(factory_run_id)

        verdicts[wo_id] = verdict.lower()
        run_store.update(
            run_id,
            factory_run_ids=factory_run_ids,
            work_order_verdicts=verdicts,
        )

        if verdict != "PASS":
            error_msg = result.get("error") or f"{wo_id} failed with verdict {verdict}"
            run_store.update(run_id, status="failed", finished_at=_ts(), error=error_msg)
            log.emit("pipeline_status", status="failed", error=error_msg)
            return

    # All WOs passed
    run_store.update(run_id, status="complete", finished_at=_ts())
    log.emit("pipeline_status", status="complete")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
