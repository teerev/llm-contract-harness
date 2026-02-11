"""CLI entry logic — orchestrates the run, creates artifact dirs, writes run_summary."""

from __future__ import annotations

import os
import sys
import traceback

from factory import defaults as _fd
from factory.console import Console
from factory.graph import build_graph
from factory.schemas import WorkOrder, load_work_order
from factory.util import (
    ARTIFACT_RUN_SUMMARY,
    ARTIFACT_WORK_ORDER,
    compute_run_id,
    save_json,
)
from factory.workspace import get_baseline_commit, is_clean, is_git_repo, rollback


def run_cli(args, console: Console | None = None) -> None:  # noqa: ANN001
    """Main entry point called by ``__main__``."""
    con = console or Console()

    repo_root = os.path.realpath(args.repo)
    work_order_path = os.path.realpath(args.work_order)
    out_dir = os.path.realpath(args.out)

    # ------------------------------------------------------------------
    # Load work order
    # ------------------------------------------------------------------
    try:
        work_order: WorkOrder = load_work_order(work_order_path)
    except Exception as exc:
        con.error(f"Failed to load work order: {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Preflight checks
    # ------------------------------------------------------------------
    if not is_git_repo(repo_root):
        con.error(f"{repo_root} is not a git repository.")
        sys.exit(1)

    if not is_clean(repo_root):
        con.error(
            f"{repo_root} has uncommitted changes. "
            "The working tree must be clean (no staged, unstaged, or untracked changes)."
        )
        sys.exit(1)

    if out_dir == repo_root or out_dir.startswith(repo_root + os.sep):
        con.error(
            f"Output directory ({out_dir}) must not be inside the product repo "
            f"({repo_root}). Artifacts written there would be affected by git rollback "
            "and could pollute the tree hash on success."
        )
        sys.exit(1)

    baseline_commit = get_baseline_commit(repo_root)

    # ------------------------------------------------------------------
    # Deterministic run_id
    # ------------------------------------------------------------------
    run_id = compute_run_id(work_order.model_dump(), baseline_commit)
    run_dir = os.path.join(out_dir, run_id)

    # M-21: refuse to overwrite a prior run's artifacts.
    prior_summary = os.path.join(run_dir, ARTIFACT_RUN_SUMMARY)
    if os.path.isfile(prior_summary):
        con.error(
            f"A run summary already exists at {prior_summary}. "
            "Re-running the same work order on the same baseline would "
            "overwrite the prior run's artifacts. To re-run, delete or "
            f"move the directory: {run_dir}"
        )
        sys.exit(1)

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
    # Console: show run header
    # ------------------------------------------------------------------
    con.header("factory run")
    con.kv("Work order", f"{work_order.id}  \"{work_order.title}\"")
    con.kv("Run ID", run_id)
    con.kv("Model", f"{args.llm_model} (temp={args.llm_temperature})")
    con.kv("Max attempts", str(args.max_attempts))
    con.kv("Baseline", baseline_commit[:12], verbose_only=True)
    con.kv("Repo", repo_root, verbose_only=True)
    con.kv("Artifacts", run_dir)

    # ------------------------------------------------------------------
    # M-22: Override verify_exempt unless explicitly allowed by operator
    # ------------------------------------------------------------------
    wo_dict = work_order.model_dump()
    if wo_dict.get("verify_exempt") and not getattr(args, "allow_verify_exempt", False):
        con.warning(
            "work order has verify_exempt=true but --allow-verify-exempt "
            "was not passed. Overriding to false — full verification will run."
        )
        wo_dict["verify_exempt"] = False

    # ------------------------------------------------------------------
    # Build & invoke graph
    # ------------------------------------------------------------------
    graph = build_graph()

    initial_state: dict = {
        "work_order": wo_dict,
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

        try:
            rollback(repo_root, baseline_commit)
        except BaseException as rb_exc:
            con.warning(
                f"Best-effort rollback failed: {rb_exc}. "
                "The repo may be in a dirty state. "
                f"Restore manually with: git -C {repo_root} reset --hard {baseline_commit} "
                f"&& git -C {repo_root} clean -fd"
            )

        # M-09: Check whether rollback actually succeeded
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
            con.critical(f"Failed to write run summary: {exc}")

        con.verdict("ERROR", f"unhandled exception: {exc}")
        con.kv("Run summary", summary_path)

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

    # ------------------------------------------------------------------
    # Console: show attempt summaries and verdict
    # ------------------------------------------------------------------
    for attempt in attempts:
        idx = attempt["attempt_index"]
        fb = attempt.get("failure_brief")
        con.attempt_start(idx, args.max_attempts)

        touched = attempt.get("touched_files", [])
        if touched:
            con.step("TR", f"wrote {len(touched)} file(s)", ", ".join(touched))

        # Verify results
        for vr in attempt.get("verify", []):
            status = "PASS" if vr.get("exit_code") == 0 else "FAIL"
            cmd_str = " ".join(vr.get("command", []))
            con.step("PO", f"verify {status}", cmd_str)

        # Acceptance results
        acc = attempt.get("acceptance", [])
        if acc:
            passed = sum(1 for a in acc if a.get("exit_code") == 0)
            total = len(acc)
            status = "PASS" if passed == total else "FAIL"
            con.step("PO", f"acceptance {status}", f"{passed}/{total}")

        if fb:
            stage = fb.get("stage", "unknown")
            excerpt = fb.get("primary_error_excerpt", "")
            con.step("", f"FAIL (stage={stage})")
            if excerpt:
                lines = excerpt.strip().splitlines()
                con.error_block(lines)
            if attempt.get("write_ok"):
                con.rollback_notice(baseline_commit)

    con.verdict(verdict)
    con.kv("Run summary", summary_path)

    if verdict != "PASS":
        sys.exit(1)
