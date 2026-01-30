"""
prototype: se -> tr -> po loop for automated code changes.

se now calls llm for code change proposals.
"""

import json
from llm import get_model
from schemas import POReport, SEPacket, ToolReport, WorkOrder

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
"""


def se_node(state: dict, model) -> dict:
    """proposes file changes to satisfy the work order."""
    wo = WorkOrder.model_validate(state["work_order"])
    body = state["work_order_body"]
    
    user = f"""\
WORK ORDER:
{wo.model_dump()}

WORK ORDER BODY:
{body}

TARGET REPO PATH:
{state["repo_path"]}
"""
    raw = model.complete(system=SE_SYSTEM, user=user)
    
    try:
        data = json.loads(raw.strip())
        pkt = SEPacket.model_validate(data)
    except Exception as e:
        pkt = SEPacket(
            summary="Invalid SE JSON; emitting no-op.",
            writes=[],
            assumptions=[f"Parse error: {type(e).__name__}: {e}"],
        )
    
    return {"se_packet": pkt.model_dump()}


def tr_node(state: dict) -> dict:
    """applies the proposed changes and runs acceptance commands."""
    pkt = SEPacket.model_validate(state["se_packet"])
    
    # stub: report success
    report = ToolReport(
        applied=[],
        blocked_writes=[],
        command_results=[],
        all_commands_ok=True,
    )
    return {"tool_report": report.model_dump()}


def po_node(state: dict) -> dict:
    """evaluates the results and decides pass or fail."""
    tr = ToolReport.model_validate(state["tool_report"])
    
    if tr.all_commands_ok:
        report = POReport(
            decision="PASS",
            reasons=["All commands passed."],
            required_fixes=[],
        )
    else:
        report = POReport(
            decision="FAIL",
            reasons=["Commands failed."],
            required_fixes=["Fix the failing commands."],
        )
    
    it = int(state.get("iteration", 0)) + 1
    return {"po_report": report.model_dump(), "iteration": it}


def run_loop(work_order: dict, body: str, repo_path: str, max_iterations: int = 5):
    """runs se -> tr -> po in a loop until pass or max iterations."""
    model = get_model()
    
    state = {
        "work_order": work_order,
        "work_order_body": body,
        "repo_path": repo_path,
        "iteration": 0,
        "max_iterations": max_iterations,
    }
    
    for i in range(max_iterations):
        state["iteration"] = i
        
        # se proposes changes
        state.update(se_node(state, model))
        
        # tr applies changes and runs commands
        state.update(tr_node(state))
        
        # po evaluates
        state.update(po_node(state))
        
        if state["po_report"]["decision"] == "PASS":
            break
    
    return state


def main():
    # minimal example
    work_order = {"title": "Test Work Order"}
    body = "Do something."
    repo_path = "."
    
    result = run_loop(work_order, body, repo_path)
    print(f"Final decision: {result['po_report']['decision']}")


if __name__ == "__main__": 
    main()
