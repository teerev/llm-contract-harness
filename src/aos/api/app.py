"""
FastAPI application for AOS.

Endpoints:
- GET /healthz: Liveness check (is the process running?)
- GET /readyz: Readiness check (can we handle requests?)
- POST /runs: Create a new run
- GET /runs/{run_id}: Get run status
- GET /runs/{run_id}/events: Get run events
"""

from typing import Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import text

from ..db import get_session, get_engine, Run, Event
from ..queue import enqueue_run
from ..events import record_event, EventKind
from .schemas import (
    CreateRunRequest,
    CreateRunResponse,
    RunResponse,
    HealthResponse,
    EventResponse,
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

@app.post("/runs", response_model=CreateRunResponse, status_code=201)
def create_run(request: CreateRunRequest):
    """
    Create a new run.
    
    This is the main entry point for submitting jobs. The run
    starts in PENDING status and will be picked up by a worker.
    """
    with get_session() as session:
        # Check idempotency key if provided
        if request.idempotency_key:
            existing = session.query(Run).filter(
                Run.idempotency_key == request.idempotency_key
            ).first()
            if existing:
                return CreateRunResponse(run_id=existing.id, status=existing.status)
        
        # Create new run
        run = Run(
            repo_url=request.repo_url,
            repo_ref=request.ref,
            work_order=request.work_order,
            work_order_body=request.work_order_body,
            params=request.params.model_dump(),
            idempotency_key=request.idempotency_key,
        )
        session.add(run)
        session.flush()  # Assigns the ID
        
        # Record creation event
        record_event(
            session, run.id, EventKind.RUN_CREATED,
            payload={
                "repo_url": request.repo_url,
                "ref": request.ref,
                "params": request.params.model_dump(),
                "writeback_mode": request.writeback.mode,
            }
        )
        
        run_id = run.id
        status = run.status
    
    # Enqueue job for worker (outside the session)
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
