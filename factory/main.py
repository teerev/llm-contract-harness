"""entry point for running the se -> tr -> po loop."""

from llm import get_model
from loop import build_graph


def main():
    work_order = {"title": "Test Work Order", "acceptance_commands": ["echo ok"]}
    body = "Create a hello.txt file with 'Hello World'."
    repo_path = "."

    result = build_graph(get_model()).invoke(
        {
            "repo_path": repo_path,
            "work_order": work_order,
            "work_order_body": body,
            "iteration": 0,
            "max_iterations": 5,
        }
    )

    po = result.get("po_report") or {}
    print(f"Final decision: {po.get('decision')}")


if __name__ == "__main__": 
    main()
