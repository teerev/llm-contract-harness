"""state machine that runs the se ->  tr -> po loop."""

import os
import subprocess
from pathlib import Path
from typing import Any, TypedDict
from langgraph.graph import END, START, StateGraph
from schemas import AppliedChange, POReport, SEPacket, ToolReport, WorkOrder
from util import matches_any_glob, normalize_rel_path, safe_join, strict_json_loads


class PrototypeState(TypedDict, total=False):
    # inputs
    repo_path: str
    work_order: dict[str, Any]
    work_order_body: str

    # loop control
    iteration: int
    max_iterations: int

    # artifacts from nodes
    se_packet: dict[str, Any]
    tool_report: dict[str, Any]
    po_report: dict[str, Any]


SE_SYSTEM = """\
You are the Software Engineer (SE).

Goal: propose the MINIMAL set of repo file changes to satisfy the work order.

Output contract (MANDATORY):
- Output MUST be a single JSON object matching:
  {
    "summary": "string",
    "writes": [{"path":"relative/path","content":"...","mode":"create|replace|delete"}, ...],
    "assumptions": ["...", ...]
  }
- No markdown. No code fences. Only JSON.

Constraints:
- Never use absolute paths or '..' segments.
- Do not modify files matching forbidden_paths.
- If allowed_paths is non-empty, only write within allowed_paths.
- Prefer smallest diffs: do not refactor unrelated code.
"""


def make_se_node(model):
    """creates an se node with the given model."""

    def se_node(state: dict) -> dict:
        wo = WorkOrder.model_validate(state["work_order"])
        body = state["work_order_body"]

        prior = ""
        po = state.get("po_report") or {}
        fixes = po.get("required_fixes") or []
        if fixes:
            prior = "\n\nPrevious FAIL required_fixes:\n- " + "\n- ".join(fixes)

        user = f"""\
WORK ORDER METADATA (dict):
{wo.model_dump()}

WORK ORDER BODY:
{body}

TARGET REPO PATH:
{state["repo_path"]}

Iteration: {state.get('iteration', 0)}{prior}
"""
        raw = model.complete(system=SE_SYSTEM, user=user)

        try:
            data = strict_json_loads(raw)
            pkt = SEPacket.model_validate(data)
        except Exception as e:
            pkt = SEPacket(
                summary="Invalid SE JSON; emitting no-op.",
                writes=[],
                assumptions=[f"Parse error: {type(e).__name__}: {e}"],
            )

        return {"se_packet": pkt.model_dump()}

    return se_node


def tool_runner_node(state: dict) -> dict:
    """applies the proposed changes and runs acceptance commands."""
    repo_root = Path(state["repo_path"]).resolve()
    wo = WorkOrder.model_validate(state["work_order"])
    pkt = SEPacket.model_validate(state["se_packet"])

    applied: list[AppliedChange] = []
    blocked: list[str] = []

    validated_writes: list[tuple] = []

    for w in pkt.writes:
        rel = normalize_rel_path(w.path)

        if matches_any_glob(rel, wo.forbidden_paths):
            blocked.append(rel)
            continue
        if wo.allowed_paths and (not matches_any_glob(rel, wo.allowed_paths)):
            blocked.append(rel)
            continue

        try:
            abs_path = safe_join(repo_root, rel)
        except Exception:
            blocked.append(rel)
            continue

        validated_writes.append((w, rel, abs_path))

    if blocked:
        report = ToolReport(
            applied=[],
            blocked_writes=blocked,
            command_results=[],
            all_commands_ok=False,
        )
        return {"tool_report": report.model_dump()}

    for w, rel, abs_path in validated_writes:
        if w.mode == "delete":
            if abs_path.exists():
                abs_path.unlink()
            applied.append(AppliedChange(path=rel, action="delete"))
            continue

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(w.content or "", encoding="utf-8")
        applied.append(AppliedChange(path=rel, action="create" if w.mode == "create" else "replace"))

    # run acceptance commands
    env = {**os.environ, **wo.env}
    results = []
    all_ok = True

    for cmd in wo.acceptance_commands:
        try:
            p = subprocess.run(
                cmd,
                shell=True,
                cwd=str(repo_root),
                text=True,
                capture_output=True,
                env=env,
                timeout=wo.command_timeout_sec,
            )
            results.append(
                {"command": cmd, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "timed_out": False}
            )
            if p.returncode != 0:
                all_ok = False
        except subprocess.TimeoutExpired:
            results.append({"command": cmd, "returncode": 124, "stdout": "", "stderr": "Timed out", "timed_out": True})
            all_ok = False

    report = ToolReport(
        applied=applied,
        blocked_writes=blocked,
        command_results=results,
        all_commands_ok=all_ok,
    )
    return {"tool_report": report.model_dump()}


def po_node(state: dict) -> dict:
    """evaluates the results and decides pass or fail."""
    tr = ToolReport.model_validate(state["tool_report"])

    reasons: list[str] = []
    fixes: list[str] = []

    if tr.blocked_writes:
        reasons.append("Some writes were blocked by path constraints.")
        fixes.append(f"Remove/avoid these paths: {tr.blocked_writes}")

    if not tr.all_commands_ok:
        reasons.append("One or more acceptance commands failed.")
        for cr in tr.command_results:
            if cr.returncode != 0:
                fixes.append(f"Fix failing command (exit={cr.returncode}): {cr.command}")

    decision = "PASS" if (not reasons and tr.all_commands_ok) else "FAIL"
    if decision == "PASS":
        reasons.append("All acceptance commands passed and no constraints were violated.")

    report = POReport(decision=decision, reasons=reasons, required_fixes=fixes)

    it = int(state.get("iteration", 0)) + 1
    return {"po_report": report.model_dump(), "iteration": it}


def conditional_route(state: dict):
    """decides next step based on po result and iteration count."""
    po = state.get("po_report") or {}
    if po.get("decision") == "PASS":
        return END
    it = int(state.get("iteration", 0))
    max_it = int(state.get("max_iterations", 5))
    if it >= max_it:
        return END
    return "SE"


def build_graph(se_model):
    """builds and returns the compiled state graph."""
    g = StateGraph(PrototypeState)

    g.add_node("SE", make_se_node(se_model))
    g.add_node("TR", tool_runner_node)
    g.add_node("PO", po_node)

    g.add_edge(START, "SE")
    g.add_edge("SE", "TR")
    g.add_edge("TR", "PO")

    g.add_conditional_edges(source="PO", path=conditional_route, path_map={"SE": "SE", END: END})
    return g.compile()
