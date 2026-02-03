"""pydantic schemas used throughout the pipeline."""

from typing import Any, Literal
from pydantic import BaseModel, Field

# Shell policy options for M7
ShellPolicy = Literal["forbidden", "warn", "allow"]


class CommandSpec(BaseModel):
    # provide argv (preferred) or cmd string. shell=true enables shell syntax.
    argv: list[str] | None = None
    cmd: str | None = None
    shell: bool = False
    timeout_sec: int | None = None


class WorkOrder(BaseModel):
    title: str = Field(default="Untitled Work Order")
    
    # Repository configuration
    repo: str | None = None  # GitHub URL (required for AOS)
    clone_branch: str = Field(default="main")  # Branch/SHA to clone from
    push_branch: str | None = None  # Branch to push results to (None = no push)
    
    # Execution limits
    max_iterations: int = Field(default=5, ge=1, le=20)
    
    # Task specification
    acceptance_commands: list[CommandSpec | str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    command_timeout_sec: int = 300
    notes: str = Field(default="")
    context_files: list[str] = Field(default_factory=list)
    
    # Quality gates (M5, M6)
    min_assertions: int = Field(default=1)  # M5: Minimum meaningful assertions required
    coverage_threshold: int | None = None  # M6: e.g., 80 for 80% coverage (None = skip)
    
    # Security policy (M7)
    shell_policy: ShellPolicy = Field(default="warn")  # M7: forbidden/warn/allow for shell=True


class FileWrite(BaseModel):
    path: str
    content: str | None = None
    mode: Literal["create", "replace", "delete"] = "replace"


class SEPacket(BaseModel):
    summary: str = Field(default="")
    writes: list[FileWrite] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class CommandResult(BaseModel):
    spec: dict[str, Any]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class AppliedChange(BaseModel):
    path: str
    action: Literal["create", "replace", "delete"]


class InvariantResult(BaseModel):
    """Result of a single invariant check."""
    passed: bool
    check_name: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class InvariantReport(BaseModel):
    """Report from all invariant checks."""
    all_passed: bool = True
    results: list[InvariantResult] = Field(default_factory=list)


class ToolReport(BaseModel):
    applied: list[AppliedChange] = Field(default_factory=list)
    blocked_writes: list[str] = Field(default_factory=list)
    command_results: list[CommandResult] = Field(default_factory=list)
    all_commands_ok: bool = False
    # Invariant check results (Layer 2 verification)
    invariant_report: InvariantReport | None = None
    all_invariants_ok: bool = True


class POReport(BaseModel):
    decision: Literal["PASS", "FAIL"] = "FAIL"
    reasons: list[str] = Field(default_factory=list)
    required_fixes: list[str] = Field(default_factory=list)
