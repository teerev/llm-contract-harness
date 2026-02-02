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

class WritebackConfig(BaseModel):
    """Configuration for pushing changes back to GitHub."""
    mode: str = Field(default="none", pattern="^(none|push_branch)$")
    branch_name: Optional[str] = None


class RunParams(BaseModel):
    """Execution parameters for a run."""
    max_iterations: int = Field(default=5, ge=1, le=20)
    model_name: Optional[str] = None


class CreateRunRequest(BaseModel):
    """
    Request body for POST /runs.
    
    The work_order_md field accepts a markdown string with YAML frontmatter.
    The repo URL is taken from the `repo:` field in the work order YAML.
    
    Example work order:
        ---
        title: Add feature
        repo: https://github.com/user/repo
        acceptance_commands:
          - pytest
        ---
        Implement the feature.
    
    The optional repo_url parameter can override the work order's repo field.
    """
    # Work order as markdown (same format as .md files)
    work_order_md: str = Field(..., description="Work order markdown with YAML frontmatter")
    
    # Git source (optional - defaults to repo field in work order)
    repo_url: Optional[str] = Field(None, description="GitHub clone URL (overrides work order repo)")
    ref: str = Field(default="main", description="Branch or commit SHA")
    
    # Execution config
    params: RunParams = Field(default_factory=RunParams)
    writeback: WritebackConfig = Field(default_factory=WritebackConfig)
    
    # Idempotency
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
