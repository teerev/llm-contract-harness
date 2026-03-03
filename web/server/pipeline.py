"""Pipeline orchestration — runs planner then factory in-process.

Called from LocalRunner in a background thread.  Writes events to the
per-run EventLog and updates RunStore metadata at each stage transition.
"""

from __future__ import annotations

import glob
import hashlib
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
    """Run the full pipeline: planner compile → factory run per WO → optional push.

    This function blocks — the caller is responsible for running it in a
    background thread.
    """
    run_dir = os.path.join(config.ARTIFACTS_DIR, "pipeline", run_id)
    events_path = run_store.events_path(run_id)
    log = EventLog(events_path)

    try:
        wo_files = _run_planner(run_id, prompt, run_dir, log, run_store)
        if wo_files:
            factory_ok = _run_factory(run_id, run_dir, wo_files, log, run_store)
            if factory_ok:
                if opts.push_to_demo and opts.branch_name:
                    _push_to_demo(run_id, run_dir, opts.branch_name, log, run_store)
                else:
                    run_store.update(run_id, status="complete", finished_at=_ts())
                    log.emit("pipeline_status", status="complete")
    except Exception as exc:
        run_store.update(run_id, status="failed", finished_at=_ts(), error=str(exc))
        log.emit("pipeline_status", status="failed", error=str(exc))
        log.emit("console", text=traceback.format_exc(), level="error")
    finally:
        log.close()
        _write_manifest(run_id, run_store)
        _upload_to_s3(run_id, run_store)


# ---------------------------------------------------------------------------
# Manifest — provenance record written after every pipeline run
# ---------------------------------------------------------------------------

def _sha256_file(path: str) -> str | None:
    """Return hex SHA-256 of a file, or None if unreadable."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _write_manifest(run_id: str, run_store: RunStore) -> None:
    """Write manifest.json with provenance data for the completed pipeline run."""
    try:
        meta = run_store.get(run_id)
    except FileNotFoundError:
        return

    run_dir = os.path.join(config.ARTIFACTS_DIR, "pipeline", run_id)
    spec_path = os.path.join(run_dir, "spec.txt")

    manifest: dict = {
        "pipeline_run_id": run_id,
        "planner_run_id": meta.planner_run_id,
        "factory_run_ids": meta.factory_run_ids,
        "status": meta.status,
        "started_at": meta.started_at,
        "finished_at": meta.finished_at,
        "work_order_count": meta.work_order_count,
        "verdicts": meta.work_order_verdicts,
        "prompt_sha256": _sha256_file(spec_path),
    }

    if meta.push_remote:
        manifest["push"] = {
            "remote": meta.push_remote,
            "branch": meta.push_branch,
            "commit_sha": meta.push_commit_sha,
            "url": meta.push_url,
        }

    if meta.error:
        manifest["error"] = meta.error

    manifest_path = os.path.join(run_dir, "manifest.json")
    try:
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass


def _upload_to_s3(run_id: str, run_store: RunStore) -> None:
    """Upload run artifacts to S3 if configured. Non-fatal on error."""
    from web.server.s3_upload import upload_run_artifacts, S3_BUCKET
    if not S3_BUCKET:
        return
    try:
        meta = run_store.get(run_id)
    except FileNotFoundError:
        return
    upload_run_artifacts(
        run_id=run_id,
        artifacts_dir=config.ARTIFACTS_DIR,
        planner_run_id=meta.planner_run_id,
        factory_run_ids=meta.factory_run_ids,
    )


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
) -> bool:
    """Execute factory for each WO sequentially. Returns True if all passed."""
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
            return False

    # All WOs passed — but don't emit complete yet if push is pending
    return True


# ---------------------------------------------------------------------------
# Push stage
# ---------------------------------------------------------------------------

def _push_to_demo(
    run_id: str,
    run_dir: str,
    branch_name: str,
    log: EventLog,
    run_store: RunStore,
) -> None:
    """Push the repo to the demo remote. This is a post-step; failure doesn't fail the pipeline."""
    push_url = config.DEMO_REMOTE_URL
    safe_url = config.DEMO_REMOTE_URL_SAFE  # credentials scrubbed — safe for logs/events

    def _scrub(text: str) -> str:
        """Remove credential-laden URL from error messages before they reach the browser."""
        return text.replace(push_url, safe_url) if push_url else text

    if not push_url:
        log.emit("console", text="Demo remote not configured, skipping push", level="warning")
        run_store.update(run_id, status="complete", finished_at=_ts())
        log.emit("pipeline_status", status="complete")
        return

    run_store.update(run_id, status="pushing")
    log.emit("pipeline_status", status="pushing")

    repo_dir = os.path.join(run_dir, "repo")

    log.emit("git_push_started", remote=safe_url, branch=branch_name)

    try:
        # Add demo remote (remove first if exists)
        subprocess.run(
            ["git", "remote", "remove", "demo"],
            cwd=repo_dir, capture_output=True, timeout=_GIT_TIMEOUT,
        )
        subprocess.run(
            ["git", "remote", "add", "demo", push_url],
            cwd=repo_dir, check=True, capture_output=True, timeout=_GIT_TIMEOUT,
        )

        # Get the current commit SHA
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir, check=True, capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
        commit_sha = result.stdout.strip()[:7]

        # Push to demo remote
        push_result = subprocess.run(
            ["git", "push", "-f", "demo", f"HEAD:refs/heads/{branch_name}"],
            cwd=repo_dir, capture_output=True, text=True, timeout=_PUSH_TIMEOUT,
        )

        if push_result.returncode != 0:
            error = _scrub(push_result.stderr.strip() or push_result.stdout.strip() or "push failed")
            log.emit(
                "git_push_done",
                ok=False,
                remote=safe_url,
                branch=branch_name,
                error=error,
            )
            log.emit("console", text=f"Push failed: {error}", level="error")
        else:
            web_url = _remote_to_web_url(safe_url, branch_name)
            log.emit(
                "git_push_done",
                ok=True,
                remote=safe_url,
                branch=branch_name,
                commit_sha=commit_sha,
                url=web_url,
            )
            log.emit("console", text=f"Pushed to {safe_url} @ {branch_name} ({commit_sha})", level="info")
            run_store.update(
                run_id,
                push_remote=safe_url,
                push_branch=branch_name,
                push_commit_sha=commit_sha,
                push_url=web_url,
            )

    except subprocess.TimeoutExpired:
        error = f"Git push timed out after {_PUSH_TIMEOUT}s — remote may be unreachable"
        log.emit(
            "git_push_done",
            ok=False,
            remote=safe_url,
            branch=branch_name,
            error=error,
        )
        log.emit("console", text=error, level="error")
    except subprocess.CalledProcessError as exc:
        error = _scrub(exc.stderr if hasattr(exc, "stderr") and exc.stderr else str(exc))
        log.emit(
            "git_push_done",
            ok=False,
            remote=safe_url,
            branch=branch_name,
            error=error,
        )
        log.emit("console", text=f"Push error: {error}", level="error")
    except Exception as exc:
        error = _scrub(str(exc))
        log.emit(
            "git_push_done",
            ok=False,
            remote=safe_url,
            branch=branch_name,
            error=error,
        )
        log.emit("console", text=f"Push error: {error}", level="error")

    # Always mark complete after push attempt (push failure is not pipeline failure)
    run_store.update(run_id, status="complete", finished_at=_ts())
    log.emit("pipeline_status", status="complete")


def _remote_to_web_url(remote: str, branch: str) -> str | None:
    """Convert a git remote URL to a web URL if possible."""
    # git@github.com:org/repo.git -> https://github.com/org/repo/tree/branch
    if remote.startswith("git@github.com:"):
        path = remote.replace("git@github.com:", "").rstrip(".git")
        return f"https://github.com/{path}/tree/{branch}"
    # https://[credentials@]github.com/org/repo.git -> web URL
    if "github.com/" in remote and remote.startswith("https://"):
        path = remote.split("github.com/", 1)[1].rstrip(".git")
        return f"https://github.com/{path}/tree/{branch}"
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GIT_TIMEOUT = 30   # seconds — local git operations
_PUSH_TIMEOUT = 60  # seconds — network push (longer for remote I/O)


def _init_repo(repo_dir: str) -> None:
    """Create a fresh git repo with one empty commit."""
    os.makedirs(repo_dir, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True, capture_output=True, timeout=_GIT_TIMEOUT)
    subprocess.run(
        ["git", "config", "user.email", "llmch@local"],
        cwd=repo_dir, check=True, capture_output=True, timeout=_GIT_TIMEOUT,
    )
    subprocess.run(
        ["git", "config", "user.name", "llmch"],
        cwd=repo_dir, check=True, capture_output=True, timeout=_GIT_TIMEOUT,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=repo_dir, check=True, capture_output=True, timeout=_GIT_TIMEOUT,
    )
