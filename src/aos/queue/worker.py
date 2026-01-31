"""
Worker job function for AOS.

This is the main job that workers execute. It:
1. Loads the run from the database
2. Clones the repository
3. Runs the factory loop
4. Records events, saves artifacts
5. Optionally pushes changes back to GitHub
"""

import os
import traceback
from datetime import datetime
from pathlib import Path
from uuid import UUID

from ..db import get_session, Run
from ..events import record_event, EventKind
from ..git import clone_repo, push_branch
from ..artifacts import save_iteration_artifacts, save_run_summary


# Where to store workspaces
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/tmp/aos/workspaces"))


def run_job(run_id: str) -> dict:
    """
    Execute a run.
    
    This is the function that RQ workers call. It handles the full
    lifecycle of a run: clone, execute factory, record results.
    
    Args:
        run_id: String UUID of the run to execute
    
    Returns:
        Dict with final status and summary
    """
    run_uuid = UUID(run_id)
    
    try:
        return _execute_run(run_uuid)
    except Exception as e:
        # Catch-all for unexpected errors
        _mark_failed(run_uuid, e)
        raise


def _execute_run(run_id: UUID) -> dict:
    """
    Internal run execution logic.
    """
    # Load run and mark as RUNNING
    with get_session() as session:
        run = session.query(Run).filter(Run.id == run_id).first()
        if not run:
            raise ValueError(f"Run not found: {run_id}")
        
        # Check if already canceled
        if run.status == "CANCELED":
            return {"status": "CANCELED", "decision": None}
        
        if run.status != "PENDING":
            raise ValueError(f"Run is not PENDING: {run.status}")
        
        run.status = "RUNNING"
        run.started_at = datetime.utcnow()
        
        # Store values we need outside the session
        repo_url = run.repo_url
        repo_ref = run.repo_ref
        work_order = dict(run.work_order)
        work_order_body = run.work_order_body
        max_iterations = run.params.get("max_iterations", 5)
        writeback_config = run.writeback or {}
        
        record_event(
            session, run_id, EventKind.RUN_START,
            payload={"repo_url": repo_url, "ref": repo_ref}
        )
    
    # Set up workspace
    workspace_dir = WORKSPACE_ROOT / str(run_id) / "repo"
    artifact_dir = WORKSPACE_ROOT / str(run_id) / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    
    # Clone repository
    try:
        git_sha = clone_repo(repo_url, workspace_dir, repo_ref)
    except Exception as e:
        with get_session() as session:
            record_event(
                session, run_id, EventKind.ERROR_EXCEPTION,
                level="ERROR",
                payload={"phase": "clone", "error": str(e)}
            )
        raise
    
    # Record git SHA
    with get_session() as session:
        run = session.query(Run).filter(Run.id == run_id).first()
        run.git_sha = git_sha
        run.artifact_root = str(WORKSPACE_ROOT / str(run_id))
    
    # Check for cancellation before starting factory
    if _is_canceled(run_id):
        _handle_cancellation(run_id)
        return {"status": "CANCELED", "decision": None}
    
    # Run the factory loop
    try:
        result = _run_factory(
            run_id=run_id,
            workspace_dir=workspace_dir,
            artifact_dir=artifact_dir,
            work_order=work_order,
            work_order_body=work_order_body,
            max_iterations=max_iterations,
        )
    except Exception as e:
        with get_session() as session:
            record_event(
                session, run_id, EventKind.ERROR_EXCEPTION,
                level="ERROR",
                payload={
                    "phase": "factory",
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
            )
        raise
    
    # Determine final status
    po_report = result.get("po_report") or {}
    decision = po_report.get("decision", "FAIL")
    final_status = "SUCCEEDED" if decision == "PASS" else "FAILED"
    
    # Handle writeback if enabled and PASS
    pushed_branch = None
    if decision == "PASS" and writeback_config.get("mode") == "push_branch":
        try:
            pushed_branch = _do_writeback(
                run_id=run_id,
                workspace_dir=workspace_dir,
                writeback_config=writeback_config,
                work_order=work_order,
            )
        except Exception as e:
            # Writeback failure doesn't fail the run, just log it
            with get_session() as session:
                record_event(
                    session, run_id, EventKind.ERROR_EXCEPTION,
                    level="WARN",
                    payload={
                        "phase": "writeback",
                        "error": str(e),
                    }
                )
    
    # Save final summary artifact
    with get_session() as session:
        save_run_summary(
            session, run_id, artifact_dir,
            {
                "run_id": str(run_id),
                "status": final_status,
                "decision": decision,
                "iterations": result.get("iteration", 0),
                "git_sha": git_sha,
                "pushed_branch": pushed_branch,
            }
        )
    
    # Update run with final status
    with get_session() as session:
        run = session.query(Run).filter(Run.id == run_id).first()
        run.status = final_status
        run.finished_at = datetime.utcnow()
        run.iteration = result.get("iteration", 0)
        run.result_summary = f"Decision: {decision}" + (f", pushed to {pushed_branch}" if pushed_branch else "")
        
        record_event(
            session, run_id, EventKind.RUN_END,
            payload={
                "status": final_status,
                "decision": decision,
                "iterations": result.get("iteration", 0),
                "pushed_branch": pushed_branch,
            }
        )
    
    return {"status": final_status, "decision": decision, "pushed_branch": pushed_branch}


def _run_factory(
    run_id: UUID,
    workspace_dir: Path,
    artifact_dir: Path,
    work_order: dict,
    work_order_body: str,
    max_iterations: int,
) -> dict:
    """
    Run the factory graph and record events.
    """
    # Import factory here to avoid import issues
    from factory.graph import build_graph
    from factory.llm import get_model
    
    # Build the graph
    graph = build_graph(get_model())
    
    # Initial state
    state = {
        "repo_path": str(workspace_dir),
        "work_order": work_order,
        "work_order_body": work_order_body,
        "iteration": 0,
        "max_iterations": max_iterations,
    }
    
    # Run the graph
    # For now, we run it as a single invoke() call
    # In future, we could use streaming to capture per-node events
    result = graph.invoke(state)
    
    # Record the final artifacts and events
    _record_iteration_events(run_id, artifact_dir, result)
    
    return result


def _record_iteration_events(run_id: UUID, artifact_dir: Path, result: dict) -> None:
    """
    Record events and save artifacts for the factory result.
    """
    with get_session() as session:
        iteration = result.get("iteration", 0)
        
        se_packet = result.get("se_packet")
        tool_report = result.get("tool_report")
        po_report = result.get("po_report")
        
        # Save artifacts
        save_iteration_artifacts(
            session, run_id, artifact_dir,
            iteration, se_packet, tool_report, po_report,
        )
        
        # SE output event
        if se_packet:
            record_event(
                session, run_id, EventKind.SE_OUTPUT,
                iteration=iteration,
                payload={
                    "summary": se_packet.get("summary", ""),
                    "writes_count": len(se_packet.get("writes", [])),
                    "assumptions_count": len(se_packet.get("assumptions", [])),
                }
            )
        
        # TR apply event
        if tool_report:
            record_event(
                session, run_id, EventKind.TR_APPLY,
                iteration=iteration,
                payload={
                    "applied_count": len(tool_report.get("applied", [])),
                    "blocked_count": len(tool_report.get("blocked_writes", [])),
                    "commands_ok": tool_report.get("all_commands_ok", False),
                }
            )
        
        # PO result event
        if po_report:
            record_event(
                session, run_id, EventKind.PO_RESULT,
                iteration=iteration,
                payload={
                    "decision": po_report.get("decision"),
                    "reasons_count": len(po_report.get("reasons", [])),
                    "fixes_count": len(po_report.get("required_fixes", [])),
                }
            )


def _do_writeback(
    run_id: UUID,
    workspace_dir: Path,
    writeback_config: dict,
    work_order: dict,
) -> str:
    """
    Push changes to a new branch on GitHub.
    
    Returns the pushed branch name.
    """
    # Determine branch name
    branch_name = writeback_config.get("branch_name")
    if not branch_name:
        # Auto-generate: aos/run-<run_id>
        short_id = str(run_id)[:8]
        branch_name = f"aos/run-{short_id}"
    
    # Commit message
    title = work_order.get("title", "AOS run")
    commit_message = f"AOS: {title} (run {run_id})"
    
    # Get author from env or use defaults
    author_name = os.environ.get("GIT_AUTHOR_NAME", "AOS")
    author_email = os.environ.get("GIT_AUTHOR_EMAIL", "aos@localhost")
    
    # Push
    push_branch(
        repo_dir=workspace_dir,
        branch_name=branch_name,
        commit_message=commit_message,
        author_name=author_name,
        author_email=author_email,
    )
    
    return branch_name


def _is_canceled(run_id: UUID) -> bool:
    """Check if a run has been canceled."""
    with get_session() as session:
        run = session.query(Run).filter(Run.id == run_id).first()
        return run and run.status == "CANCELED"


def _handle_cancellation(run_id: UUID) -> None:
    """Handle a canceled run."""
    with get_session() as session:
        run = session.query(Run).filter(Run.id == run_id).first()
        if run:
            run.finished_at = datetime.utcnow()
            record_event(
                session, run_id, EventKind.RUN_CANCELED,
                payload={"reason": "Canceled by user"}
            )


def _mark_failed(run_id: UUID, error: Exception) -> None:
    """
    Mark a run as failed due to an exception.
    """
    try:
        with get_session() as session:
            run = session.query(Run).filter(Run.id == run_id).first()
            if run:
                run.status = "FAILED"
                run.finished_at = datetime.utcnow()
                run.error = {
                    "type": type(error).__name__,
                    "message": str(error),
                }
                
                record_event(
                    session, run_id, EventKind.RUN_END,
                    level="ERROR",
                    payload={
                        "status": "FAILED",
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                    }
                )
    except Exception:
        # Don't let error recording cause another exception
        pass
