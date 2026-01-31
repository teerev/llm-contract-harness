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
    
    Required: repo_url and work_order
    Optional: everything else has sensible defaults
    """
    # Git source
    repo_url: str = Field(..., description="GitHub clone URL")
    ref: str = Field(default="main", description="Branch or commit SHA")
    
    # Work order (the task)
    work_order: dict[str, Any] = Field(..., description="Structured work order")
    work_order_body: str = Field(default="", description="Work order body text")
    
    # Optional: markdown alternative (not implemented yet)
    # work_order_md: Optional[str] = None
    
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
