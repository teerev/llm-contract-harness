"""CLI entry logic — orchestrates the run, creates artifact dirs, writes run_summary."""

from __future__ import annotations

import os
import sys

from factory.graph import build_graph
from factory.schemas import WorkOrder, load_work_order
from factory.util import compute_run_id, save_json
from factory.workspace import get_baseline_commit, is_clean, is_git_repo


def run_cli(args) -> None:  # noqa: ANN001 — argparse.Namespace
    """Main entry point called by ``__main__``."""
    repo_root = os.path.realpath(args.repo)
    work_order_path = os.path.realpath(args.work_order)
    out_dir = os.path.realpath(args.out)

    # ------------------------------------------------------------------
    # Load work order
    # ------------------------------------------------------------------
    try:
        work_order: WorkOrder = load_work_order(work_order_path)
    except Exception as exc:
        print(f"ERROR: Failed to load work order: {exc}", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # Preflight checks
    # ------------------------------------------------------------------
    if not is_git_repo(repo_root):
        print(f"ERROR: {repo_root} is not a git repository.", file=sys.stderr)
        sys.exit(1)

    if not is_clean(repo_root):
        print(
            f"ERROR: {repo_root} has uncommitted changes. "
            "The working tree must be clean (no staged, unstaged, or untracked changes).",
            file=sys.stderr,
        )
        sys.exit(1)

    baseline_commit = get_baseline_commit(repo_root)

    # ------------------------------------------------------------------
    # Deterministic run_id
    # ------------------------------------------------------------------
    run_id = compute_run_id(work_order.model_dump(), baseline_commit)
    run_dir = os.path.join(out_dir, run_id)
    os.makedirs(run_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Build & invoke graph
    # ------------------------------------------------------------------
    graph = build_graph()

    initial_state: dict = {
        "work_order": work_order.model_dump(),
        "repo_root": repo_root,
        "baseline_commit": baseline_commit,
        "max_attempts": args.max_attempts,
        "timeout_seconds": args.timeout_seconds,
        "llm_model": args.llm_model,
        "llm_temperature": args.llm_temperature,
        "out_dir": out_dir,
        "run_id": run_id,
        # Per-attempt state (initial)
        "attempt_index": 1,
        "proposal": None,
        "touched_files": [],
        "write_ok": False,
        "failure_brief": None,
        "verify_results": [],
        "acceptance_results": [],
        # Accumulated state
        "attempts": [],
        "verdict": "",
        "repo_tree_hash_after": None,
    }

    final_state = graph.invoke(initial_state)

    # ------------------------------------------------------------------
    # Write run_summary.json
    # ------------------------------------------------------------------
    verdict = final_state.get("verdict", "FAIL")
    attempts = final_state.get("attempts", [])

    summary_dict = {
        "run_id": run_id,
        "work_order_id": work_order.id,
        "verdict": verdict,
        "total_attempts": len(attempts),
        "baseline_commit": baseline_commit,
        "repo_tree_hash_after": final_state.get("repo_tree_hash_after"),
        "attempts": attempts,
    }

    summary_path = os.path.join(run_dir, "run_summary.json")
    save_json(summary_dict, summary_path)

    print(f"Verdict: {verdict}")
    print(f"Run summary: {summary_path}")

    if verdict != "PASS":
        sys.exit(1)
