"""
FastAPI application for AOS.

Endpoints:
- GET /healthz: Liveness check (is the process running?)
- GET /readyz: Readiness check (can we handle requests?)
- POST /runs: Create a new run
- GET /runs/{run_id}: Get run status
- GET /runs/{run_id}/events: Get run events
"""

import logging
from pathlib import Path
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Form, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import text

from factory.workspace import parse_work_order
from ..db import get_session, get_engine, Run, Event, Artifact
from ..queue import enqueue_run
from ..events import record_event, EventKind
from ..validators import validate_repo_url, validate_work_order, validate_ref, validate_branch_name
from .schemas import (
    CreateRunRequest,
    CreateRunResponse,
    RunResponse,
    HealthResponse,
    EventResponse,
    ArtifactResponse,
    CancelResponse,
)


app = FastAPI(
    title="AOS",
    description="Agent Orchestration Service",
    version="0.1.0",
)


# ============================================================
# Health endpoints
# ============================================================

@app.get("/healthz", response_model=HealthResponse)
def healthz():
    """
    Liveness check.
    
    Returns 200 if the process is running. Used by load balancers
    and container orchestrators to know the process is alive.
    """
    return HealthResponse(status="ok")


@app.get("/readyz", response_model=HealthResponse)
def readyz():
    """
    Readiness check.
    
    Returns 200 only if we can connect to the database.
    Used to know when the service is ready to handle requests.
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return HealthResponse(status="ok", details={"database": "connected"})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database not ready: {e}")


# ============================================================
# Run endpoints
# ============================================================

def _resolve_repo_url(work_order: dict, override_url: str | None) -> str:
    """
    Resolve the repo URL from work order or override.
    
    Priority: override_url > work_order['repo']
    Validates that the result is a valid GitHub URL.
    """
    repo_url = override_url or work_order.get("repo")
    
    if not repo_url:
        raise HTTPException(
            status_code=400,
            detail="No repo specified. Add 'repo: https://github.com/...' to your work order YAML."
        )
    
    # Validate using centralized validator
    try:
        validate_repo_url(repo_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    return repo_url


@app.post("/runs", response_model=CreateRunResponse, status_code=201)
def create_run(request: CreateRunRequest):
    """
    Create a new run.
    
    This is the main entry point for submitting jobs. The run
    starts in PENDING status and will be picked up by a worker.
    
    The repo URL is taken from the `repo:` field in the work order YAML.
    You can optionally override it with the `repo_url` parameter.
    """
    # Parse markdown using the same function as the factory CLI
    try:
        work_order_model, work_order_body = parse_work_order(request.work_order_md)
        work_order = work_order_model.model_dump()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid work order format: {e}"
        )
    
    # Validate work order for security issues (logs warnings, doesn't reject)
    validate_work_order(work_order)
    
    # Resolve repo URL (from work order or override)
    repo_url = _resolve_repo_url(work_order, request.repo_url)
    
    with get_session() as session:
        # Check idempotency key if provided
        if request.idempotency_key:
            existing = session.query(Run).filter(
                Run.idempotency_key == request.idempotency_key
            ).first()
            if existing:
                return CreateRunResponse(run_id=existing.id, status=existing.status)
        
        # Create new run with parsed work order
        run = Run(
            repo_url=repo_url,
            repo_ref=request.ref,
            work_order=work_order,
            work_order_body=work_order_body,
            params=request.params.model_dump(),
            writeback=request.writeback.model_dump(),
            idempotency_key=request.idempotency_key,
        )
        session.add(run)
        session.flush()  # Assigns the ID
        
        # Record creation event
        record_event(
            session, run.id, EventKind.RUN_CREATED,
            payload={
                "repo_url": repo_url,
                "ref": request.ref,
                "title": work_order.get("title", "Untitled"),
                "params": request.params.model_dump(),
                "writeback_mode": request.writeback.mode,
            }
        )
        
        run_id = run.id
        status = run.status
    
    # Enqueue job for worker (outside the session)
    enqueue_run(run_id)
    
    return CreateRunResponse(run_id=run_id, status=status)


@app.post("/runs/submit", response_model=CreateRunResponse, status_code=201)
async def submit_run(
    work_order_md: str = Form(None, description="Work order markdown (use this OR work_order_file)"),
    work_order_file: UploadFile = File(None, description="Work order .md file upload"),
    repo_url: str = Form(None, description="GitHub clone URL (optional, overrides work order repo)"),
    ref: str = Form("main", description="Branch or commit SHA"),
    max_iterations: int = Form(5, ge=1, le=20),
    writeback_mode: str = Form("none", pattern="^(none|push_branch)$"),
    branch_name: str = Form(None, description="Branch name for writeback"),
):
    """
    Submit a run using form data (simpler than JSON for curl).
    
    The repo URL is taken from the `repo:` field in the work order YAML.
    
    Usage with file upload:
        curl -X POST http://localhost:8000/runs/submit \\
          -F "work_order_md=<task.md"
    
    Or override the repo from work order:
        curl -X POST http://localhost:8000/runs/submit \\
          -F "work_order_md=<task.md" \\
          -F "repo_url=https://github.com/other/repo"
    """
    # Get markdown from either source
    if work_order_file:
        content = await work_order_file.read()
        md_text = content.decode("utf-8")
    elif work_order_md:
        md_text = work_order_md
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either work_order_md or work_order_file"
        )
    
    # Parse markdown
    try:
        work_order_model, work_order_body = parse_work_order(md_text)
        work_order = work_order_model.model_dump()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid work order format: {e}")
    
    # Validate work order for security issues (logs warnings, doesn't reject)
    validate_work_order(work_order)
    
    # Validate ref parameter
    try:
        validate_ref(ref)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Validate branch name if provided
    if branch_name:
        try:
            validate_branch_name(branch_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    
    # Resolve repo URL (from work order or override)
    resolved_repo_url = _resolve_repo_url(work_order, repo_url)
    
    # Create run
    with get_session() as session:
        run = Run(
            repo_url=resolved_repo_url,
            repo_ref=ref,
            work_order=work_order,
            work_order_body=work_order_body,
            params={"max_iterations": max_iterations},
            writeback={"mode": writeback_mode, "branch_name": branch_name},
        )
        session.add(run)
        session.flush()
        
        record_event(
            session, run.id, EventKind.RUN_CREATED,
            payload={
                "repo_url": resolved_repo_url,
                "ref": ref,
                "title": work_order.get("title", "Untitled"),
                "writeback_mode": writeback_mode,
            }
        )
        
        run_id = run.id
        status = run.status
    
    enqueue_run(run_id)
    return CreateRunResponse(run_id=run_id, status=status)


@app.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID):
    """
    Get the status of a run.
    
    Clients poll this endpoint to track progress.
    """
    with get_session() as session:
        run = session.query(Run).filter(Run.id == run_id).first()
        
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        
        return RunResponse(
            run_id=run.id,
            status=run.status,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            repo_url=run.repo_url,
            repo_ref=run.repo_ref,
            git_sha=run.git_sha,
            iteration=run.iteration,
            result_summary=run.result_summary,
            error=run.error,
        )


@app.get("/runs/{run_id}/events", response_model=list[EventResponse])
def get_events(
    run_id: UUID,
    after_id: Optional[int] = Query(None, description="Return events after this ID (for tailing)"),
):
    """
    Get events for a run.
    
    Use after_id to poll for new events (tailing).
    """
    with get_session() as session:
        # Verify run exists
        run = session.query(Run).filter(Run.id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        
        # Query events
        query = session.query(Event).filter(Event.run_id == run_id)
        
        if after_id is not None:
            query = query.filter(Event.id > after_id)
        
        events = query.order_by(Event.id).all()
        
        return [
            EventResponse(
                id=e.id,
                ts=e.ts,
                level=e.level,
                kind=e.kind,
                iteration=e.iteration,
                payload=e.payload,
            )
            for e in events
        ]


@app.post("/runs/{run_id}/cancel", response_model=CancelResponse)
def cancel_run(run_id: UUID):
    """
    Cancel a run.
    
    Cancellation is cooperative - the worker checks for cancellation
    between iterations. If the run is already terminal, this is a no-op.
    """
    with get_session() as session:
        run = session.query(Run).filter(Run.id == run_id).first()
        
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        
        # Check if already terminal
        terminal_statuses = {"SUCCEEDED", "FAILED", "CANCELED"}
        if run.status in terminal_statuses:
            return CancelResponse(run_id=run.id, status=run.status, canceled=False)
        
        # Mark as canceled
        run.status = "CANCELED"
        
        return CancelResponse(run_id=run.id, status="CANCELED", canceled=True)


# ============================================================
# Artifact endpoints
# ============================================================

@app.get("/runs/{run_id}/artifacts", response_model=list[ArtifactResponse])
def list_artifacts(run_id: UUID):
    """
    List artifacts for a run.
    """
    with get_session() as session:
        run = session.query(Run).filter(Run.id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        
        artifacts = session.query(Artifact).filter(Artifact.run_id == run_id).all()
        
        return [
            ArtifactResponse(
                id=a.id,
                name=a.name,
                content_type=a.content_type,
                bytes=a.bytes,
                sha256=a.sha256,
                created_at=a.created_at,
            )
            for a in artifacts
        ]


@app.get("/runs/{run_id}/artifacts/{name}")
def get_artifact(run_id: UUID, name: str):
    """
    Download an artifact by name.
    """
    with get_session() as session:
        run = session.query(Run).filter(Run.id == run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        
        artifact = session.query(Artifact).filter(
            Artifact.run_id == run_id,
            Artifact.name == name,
        ).first()
        
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        
        file_path = Path(artifact.path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Artifact file not found on disk")
        
        return FileResponse(
            path=file_path,
            media_type=artifact.content_type or "application/octet-stream",
            filename=artifact.name,
        )
