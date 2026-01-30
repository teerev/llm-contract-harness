"""
prototype: se -> tr -> po loop for automated code changes.
"""

from schemas import POReport, SEPacket, ToolReport, WorkOrder


def se_node(state: dict) -> dict:
    """proposes file changes to satisfy the work order."""
    wo = WorkOrder.model_validate(state["work_order"])
    
    # stub: just echo back a simple proposal
    pkt = SEPacket(
        summary=f"Stub SE response for: {wo.title}",
        writes=[],
        assumptions=[],
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
    
    return {"po_report": report.model_dump()}


def run_loop(work_order: dict, body: str, repo_path: str, max_iterations: int = 5):
    """runs se -> tr -> po in a loop until pass or max iterations."""
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
        state.update(se_node(state))
        
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
