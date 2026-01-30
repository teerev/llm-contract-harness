"""
prototype: se -> tr -> po loop for automated code changes.

this is the initial skeleton - all logic in one file.
"""


def se_node(state: dict) -> dict:
    """proposes file changes to satisfy the work order."""
    # stub: just echo back a simple proposal
    return {
        "se_packet": {
            "summary": "Stub SE response",
            "writes": [],
            "assumptions": [],
        }
    }


def tr_node(state: dict) -> dict:
    """applies the proposed changes and runs acceptance commands."""
    # stub: report success
    return {
        "tool_report": {
            "applied": [],
            "blocked_writes": [],
            "command_results": [],
            "all_commands_ok": True,
        }
    }


def po_node(state: dict) -> dict:
    """evaluates the results and decides pass or fail."""
    tr = state.get("tool_report", {})
    
    if tr.get("all_commands_ok", False):
        decision = "PASS"
        reasons = ["All commands passed."]
    else:
        decision = "FAIL"
        reasons = ["Commands failed."]
    
    return {
        "po_report": {
            "decision": decision,
            "reasons": reasons,
            "required_fixes": [],
        }
    }


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
