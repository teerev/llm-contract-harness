"""
Worker job function for AOS.

This is the main job that workers execute. It:
1. Loads the run from the database
2. Clones the repository
3. Runs the factory loop
4. Records events, saves artifacts
5. Optionally pushes changes back to GitHub
"""

import logging
import os
import traceback
from datetime import datetime
from pathlib import Path
from uuid import UUID

from ..db import get_session, Run
from ..events import record_event, EventKind
from ..git import clone_repo, push_branch
from ..artifacts import save_artifact, save_run_summary


logger = logging.getLogger(__name__)


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
        logger.error(f"Clone failed for run {run_id}: {e}", exc_info=True)
        with get_session() as session:
            record_event(
                session, run_id, EventKind.ERROR_EXCEPTION,
                level="ERROR",
                payload={
                    "phase": "clone",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback": traceback.format_exc(),
                }
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
        logger.error(f"Factory failed for run {run_id}: {e}", exc_info=True)
        with get_session() as session:
            record_event(
                session, run_id, EventKind.ERROR_EXCEPTION,
                level="ERROR",
                payload={
                    "phase": "factory",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
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
            logger.warning(f"Writeback failed for run {run_id}: {e}", exc_info=True)
            with get_session() as session:
                record_event(
                    session, run_id, EventKind.ERROR_EXCEPTION,
                    level="WARN",
                    payload={
                        "phase": "writeback",
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "traceback": traceback.format_exc(),
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
    Run the factory graph and record events using streaming.
    
    Uses LangGraph streaming to capture artifacts and events for EVERY iteration,
    not just the final one. This enables debugging why earlier iterations failed.
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
    
    logger.info(f"Starting factory for run {run_id} (max_iterations={max_iterations})")
    
    # Track current iteration (starts at 1 for human-readable naming)
    current_iteration = 1
    
    # Record iteration start event
    with get_session() as session:
        record_event(
            session, run_id, EventKind.ITERATION_START,
            iteration=current_iteration,
            payload={"max_iterations": max_iterations}
        )
    
    # Use streaming to capture each node's output as it happens
    final_state = state
    for event in graph.stream(state):
        # Each event is a dict like {"SE": {...}} or {"TR": {...}} or {"PO": {...}}
        node_name = list(event.keys())[0]
        node_output = event[node_name]
        
        # Record event and save artifact based on node type
        _record_node_output(
            run_id=run_id,
            artifact_dir=artifact_dir,
            node_name=node_name,
            node_output=node_output,
            iteration=current_iteration,
        )
        
        # Update state with node output
        final_state = {**final_state, **node_output}
        
        # PO node: check if we're looping (decision != PASS means retry)
        if node_name == "PO":
            po_report = node_output.get("po_report", {})
            decision = po_report.get("decision")
            if decision != "PASS":
                # We're about to loop back to SE for the next iteration
                current_iteration += 1
                with get_session() as session:
                    record_event(
                        session, run_id, EventKind.ITERATION_START,
                        iteration=current_iteration,
                        payload={"max_iterations": max_iterations}
                    )
                logger.info(f"Starting iteration {current_iteration} for run {run_id}")
    
    final_iteration = final_state.get("iteration", 0)
    logger.info(f"Factory completed for run {run_id} at iteration {final_iteration}")
    
    return final_state


def _record_node_output(
    run_id: UUID,
    artifact_dir: Path,
    node_name: str,
    node_output: dict,
    iteration: int,
) -> None:
    """
    Record event and save artifact for a single node output.
    
    This is called for each node as it completes during streaming,
    enabling per-iteration artifact capture.
    """
    # Node output key mapping (extensible for future nodes like VF in M14)
    NODE_OUTPUT_KEYS = {
        "SE": "se_packet",
        "TR": "tool_report",
        "PO": "po_report",
        # "VF": "verifier_report",  # Enable when M14 is implemented
    }
    
    # Event kind mapping
    NODE_EVENT_KINDS = {
        "SE": EventKind.SE_OUTPUT,
        "TR": EventKind.TR_APPLY,
        "PO": EventKind.PO_RESULT,
        # "VF": EventKind.VF_RESULT,  # Add EventKind when M14 is implemented
    }
    
    if node_name not in NODE_OUTPUT_KEYS:
        # Unknown node, skip
        return
    
    output_key = NODE_OUTPUT_KEYS[node_name]
    event_kind = NODE_EVENT_KINDS[node_name]
    artifact_data = node_output.get(output_key)
    
    if not artifact_data:
        return
    
    with get_session() as session:
        # Save artifact
        save_artifact(
            session, run_id, artifact_dir,
            f"{output_key}_iter_{iteration}.json",
            artifact_data,
        )
        
        # Record event with appropriate payload
        payload = _build_event_payload(node_name, artifact_data)
        record_event(
            session, run_id, event_kind,
            iteration=iteration,
            payload=payload,
        )


def _build_event_payload(node_name: str, data: dict) -> dict:
    """Build event payload based on node type."""
    if node_name == "SE":
        return {
            "summary": data.get("summary", ""),
            "writes_count": len(data.get("writes", [])),
            "assumptions_count": len(data.get("assumptions", [])),
        }
    elif node_name == "TR":
        return {
            "applied_count": len(data.get("applied", [])),
            "blocked_count": len(data.get("blocked_writes", [])),
            "commands_ok": data.get("all_commands_ok", False),
            "invariants_ok": data.get("all_invariants_ok", True),
        }
    elif node_name == "PO":
        return {
            "decision": data.get("decision"),
            "reasons_count": len(data.get("reasons", [])),
            "fixes_count": len(data.get("required_fixes", [])),
        }
    # Future: VF node
    # elif node_name == "VF":
    #     return {
    #         "decision": data.get("decision"),
    #         "confidence": data.get("confidence", 0.0),
    #         "coverage_gaps_count": len(data.get("coverage_gaps", [])),
    #     }
    return {}


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
    # Log the error before attempting to record it (in case DB write fails)
    logger.error(
        f"Run {run_id} failed with {type(error).__name__}: {error}",
        exc_info=True
    )
    
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
    except Exception as record_error:
        # Log the secondary error but don't let it propagate
        logger.error(
            f"Failed to record error for run {run_id}: {record_error}",
            exc_info=True
        )
