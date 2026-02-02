"""
Pydantic schemas for API request/response validation.

These define the exact shape of data going in and out of the API.
Pydantic automatically validates incoming JSON against these schemas.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================
# Request schemas (what clients send to us)
# ============================================================

class CreateRunRequest(BaseModel):
    """
    Request body for POST /runs.
    
    The work_order_md field accepts a markdown string with YAML frontmatter.
    All configuration is taken from the work order - no other parameters needed.
    
    Example work order:
        ---
        title: Add feature
        repo: https://github.com/user/repo
        clone_branch: main
        push_branch: aos/feature-branch
        max_iterations: 5
        acceptance_commands:
          - pytest
        ---
        Implement the feature.
    """
    # Work order as markdown (the single source of truth)
    work_order_md: str = Field(..., description="Work order markdown with YAML frontmatter")
    
    # Idempotency (optional, for safe retries)
    idempotency_key: Optional[str] = None


# ============================================================
# Response schemas (what we send back to clients)
# ============================================================

class CreateRunResponse(BaseModel):
    """Response for POST /runs."""
    run_id: UUID
    status: str


class RunResponse(BaseModel):
    """
    Response for GET /runs/{run_id}.
    
    Full status of a run including progress and results.
    """
    run_id: UUID
    status: str
    
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    
    repo_url: str
    repo_ref: str
    git_sha: Optional[str] = None
    
    iteration: int
    rq_job_id: Optional[str] = None  # RQ job ID for debugging
    
    result_summary: Optional[str] = None
    error: Optional[dict[str, Any]] = None


class HealthResponse(BaseModel):
    """Response for health check endpoints."""
    status: str
    details: Optional[dict[str, Any]] = None


class EventResponse(BaseModel):
    """Response for GET /runs/{run_id}/events."""
    id: int
    ts: datetime
    level: str
    kind: str
    iteration: Optional[int] = None
    payload: Optional[dict[str, Any]] = None


class CancelResponse(BaseModel):
    """Response for POST /runs/{run_id}/cancel."""
    run_id: UUID
    status: str
    canceled: bool  # True if this request caused the cancellation


class ArtifactResponse(BaseModel):
    """Response for GET /runs/{run_id}/artifacts."""
    id: int
    name: str
    content_type: Optional[str] = None
    bytes: Optional[int] = None
    sha256: Optional[str] = None
    created_at: datetime
