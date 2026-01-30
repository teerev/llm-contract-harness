"""entry point for running the se -> tr -> po loop."""

from datetime import UTC, datetime
from pathlib import Path
from llm import get_model
from loop import build_graph
from workspace import load_work_order, prepare_workspace


def main():
    work_order_path = "work_order.md"
    
    if Path(work_order_path).exists():
        wo, body = load_work_order(work_order_path)
        repo_path = wo.repo or "."
    else:
        # fallback to hardcoded example
        wo = {"title": "Test Work Order", "acceptance_commands": ["echo ok"]}
        body = "Create a hello.txt file with 'Hello World'."
        repo_path = "."
    
    product_repo = Path(repo_path).resolve()
    
    # create workspace
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    workspace_root = Path.home() / ".langgraph-prototype" / "workspaces"
    workspace = prepare_workspace(product_repo, workspace_root, run_id)

    result = build_graph(get_model()).invoke(
        {
            "repo_path": str(workspace),
            "work_order": wo if isinstance(wo, dict) else wo.model_dump(),
            "work_order_body": body,
            "iteration": 0,
            "max_iterations": 5,
        }
    )

    po = result.get("po_report") or {}
    print(f"\nFINAL: {po.get('decision')}")
    print(f"Product repo: {product_repo}")
    print(f"Workspace:    {workspace}")


if __name__ == "__main__": 
    main()
