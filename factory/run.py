"""CLI entry logic — orchestrates the run, creates artifact dirs, writes run_summary."""

from __future__ import annotations

import os
import sys
import traceback

from factory import defaults as _fd
from factory.graph import build_graph
from factory.schemas import WorkOrder, load_work_order
from factory.util import (
    ARTIFACT_RUN_SUMMARY,
    ARTIFACT_WORK_ORDER,
    compute_run_id,
    save_json,
)
from factory.workspace import get_baseline_commit, is_clean, is_git_repo, rollback


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

    if out_dir == repo_root or out_dir.startswith(repo_root + os.sep):
        print(
            f"ERROR: Output directory ({out_dir}) must not be inside the product repo "
            f"({repo_root}). Artifacts written there would be affected by git rollback "
            "and could pollute the tree hash on success.",
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

    # Persist the work order and CLI config for post-mortem reproducibility
    save_json(work_order.model_dump(), os.path.join(run_dir, ARTIFACT_WORK_ORDER))

    run_config = {
        "llm_model": args.llm_model,
        "llm_temperature": args.llm_temperature,
        "max_attempts": args.max_attempts,
        "timeout_seconds": args.timeout_seconds,
        "repo_root": repo_root,
        "out_dir": out_dir,
        "defaults": {
            "default_max_attempts": _fd.DEFAULT_MAX_ATTEMPTS,
            "default_llm_temperature": _fd.DEFAULT_LLM_TEMPERATURE,
            "default_timeout_seconds": _fd.DEFAULT_TIMEOUT_SECONDS,
            "default_llm_timeout": _fd.DEFAULT_LLM_TIMEOUT,
            "run_id_hex_length": _fd.RUN_ID_HEX_LENGTH,
            "max_file_write_bytes": _fd.MAX_FILE_WRITE_BYTES,
            "max_total_write_bytes": _fd.MAX_TOTAL_WRITE_BYTES,
            "max_json_payload_bytes": _fd.MAX_JSON_PAYLOAD_BYTES,
            "max_context_bytes": _fd.MAX_CONTEXT_BYTES,
            "max_context_files": _fd.MAX_CONTEXT_FILES,
            "max_excerpt_chars": _fd.MAX_EXCERPT_CHARS,
            "git_timeout_seconds": _fd.GIT_TIMEOUT_SECONDS,
        },
    }

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

    try:
        final_state = graph.invoke(initial_state)
    except BaseException as exc:
        # ------------------------------------------------------------------
        # Emergency handling: best-effort rollback + write summary
        # M-02: Catch BaseException (not just Exception) so that
        # KeyboardInterrupt during TR writes still triggers rollback
        # instead of leaving the repo dirty.
        # ------------------------------------------------------------------
        error_detail = traceback.format_exc()

        # Best-effort rollback — the repo may have writes applied.
        # Guard against rollback itself failing (e.g. locked index).
        # Use BaseException so a second Ctrl-C during cleanup doesn't
        # defeat the rollback.
        try:
            rollback(repo_root, baseline_commit)
        except BaseException as rb_exc:
            print(
                f"WARNING: Best-effort rollback failed: {rb_exc}. "
                "The repo may be in a dirty state. "
                f"Restore manually with: git -C {repo_root} reset --hard {baseline_commit} "
                f"&& git -C {repo_root} clean -fd",
                file=sys.stderr,
            )

        # M-09: Check whether rollback actually succeeded and record the
        # result as a machine-readable field in the summary.
        try:
            _rollback_ok = is_clean(repo_root)
        except BaseException:
            _rollback_ok = False
        if not _rollback_ok:
            _remediation = (
                f"git -C {repo_root} reset --hard {baseline_commit} "
                f"&& git -C {repo_root} clean -fdx"
            )
        else:
            _remediation = None

        # Write an emergency run_summary so the run is never invisible.
        summary_dict = {
            "run_id": run_id,
            "work_order_id": work_order.id,
            "verdict": "ERROR",
            "total_attempts": 0,
            "baseline_commit": baseline_commit,
            "repo_tree_hash_after": None,
            "rollback_failed": not _rollback_ok,
            "remediation": _remediation,
            "config": run_config,
            "attempts": [],
            "error": str(exc),
            "error_traceback": error_detail,
        }
        summary_path = os.path.join(run_dir, ARTIFACT_RUN_SUMMARY)
        try:
            save_json(summary_dict, summary_path)
        except BaseException:
            print(
                f"CRITICAL: Failed to write run summary: {exc}",
                file=sys.stderr,
            )

        print(f"Verdict: ERROR (unhandled exception)", file=sys.stderr)
        print(f"Exception: {exc}", file=sys.stderr)
        print(f"Run summary: {summary_path}", file=sys.stderr)

        # Type-specific exit codes (M-02):
        if isinstance(exc, KeyboardInterrupt):
            sys.exit(130)  # Standard SIGINT exit code (128 + 2)
        elif isinstance(exc, SystemExit):
            raise  # Preserve the original exit code
        else:
            sys.exit(2)

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
        "rollback_failed": False,  # M-09: explicitly False on normal path
        "config": run_config,
        "attempts": attempts,
    }

    summary_path = os.path.join(run_dir, ARTIFACT_RUN_SUMMARY)
    save_json(summary_dict, summary_path)

    print(f"Verdict: {verdict}")
    print(f"Run summary: {summary_path}")

    if verdict != "PASS":
        sys.exit(1)
