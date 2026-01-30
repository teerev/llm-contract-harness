"""pydantic models used throughout the pipeline."""

from typing import Any, Literal
from pydantic import BaseModel, Field


class WorkOrder(BaseModel):
    title: str = Field(default="Untitled Work Order")
    acceptance_commands: list[str] = Field(default_factory=list) 
    forbidden_paths: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    command_timeout_sec: int = 300


class FileWrite(BaseModel):
    path: str
    content: str | None = None
    mode: Literal["create", "replace", "delete"] = "replace"


class SEPacket(BaseModel):
    summary: str = Field(default="")
    writes: list[FileWrite] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class CommandResult(BaseModel):
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class AppliedChange(BaseModel):
    path: str
    action: Literal["create", "replace", "delete"]


class ToolReport(BaseModel):
    applied: list[AppliedChange] = Field(default_factory=list)
    blocked_writes: list[str] = Field(default_factory=list)
    command_results: list[CommandResult] = Field(default_factory=list)
    all_commands_ok: bool = False


class POReport(BaseModel):
    decision: Literal["PASS", "FAIL"] = "FAIL"
    reasons: list[str] = Field(default_factory=list)
    required_fixes: list[str] = Field(default_factory=list)
