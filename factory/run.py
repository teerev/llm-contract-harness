"""CLI entry logic — orchestrates the run, creates artifact dirs, writes run_summary."""

from __future__ import annotations

import json
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
    save_json,
    sha256_file,
)
from factory.workspace import get_baseline_commit, is_clean, is_git_repo, rollback
from shared.run_context import (
    generate_ulid,
    get_tool_version,
    resolve_artifacts_root,
    sha256_json,
    utc_now_iso,
    write_run_json,
)


def run_cli(args, console: Console | None = None) -> None:  # noqa: ANN001
    """Main entry point called by ``__main__``."""
    con = console or Console()

    repo_root = os.path.realpath(args.repo)
    work_order_path = os.path.realpath(args.work_order)
    export_dir = os.path.realpath(args.out) if args.out else None

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

    artifacts_root = resolve_artifacts_root(
        getattr(args, "artifacts_dir", None)
    )

    if export_dir and (export_dir == repo_root or export_dir.startswith(repo_root + os.sep)):
        con.error(
            f"Export directory ({export_dir}) must not be inside the product repo "
            f"({repo_root}). Artifacts written there would be affected by git rollback "
            "and could pollute the tree hash on success."
        )
        sys.exit(1)

    baseline_commit = get_baseline_commit(repo_root)

    # ------------------------------------------------------------------
    # ULID-based run_id — immutable per-run directory
    # ------------------------------------------------------------------
    run_id = generate_ulid()
    run_dir = os.path.join(artifacts_root, "factory", run_id)
    os.makedirs(run_dir, exist_ok=False)

    # For the graph state, out_dir points to the canonical factory parent
    # so that make_attempt_dir(out_dir, run_id, idx) produces the right path.
    out_dir = os.path.join(artifacts_root, "factory")

    # Persist the work order and CLI config for post-mortem reproducibility
    save_json(work_order.model_dump(), os.path.join(run_dir, ARTIFACT_WORK_ORDER))

    # ------------------------------------------------------------------
    # Read provenance from work order (if planner injected it)
    # ------------------------------------------------------------------
    wo_dict_raw = work_order.model_dump()
    planner_ref = None
    try:
        with open(work_order_path, "r", encoding="utf-8") as fh:
            wo_file_data = json.load(fh)
        prov = wo_file_data.get("provenance")
        if isinstance(prov, dict) and prov.get("planner_run_id"):
            planner_ref = {
                "planner_run_id": prov.get("planner_run_id"),
                "compile_hash": prov.get("compile_hash"),
                "manifest_sha256": prov.get("manifest_sha256"),
            }
    except Exception:
        pass  # best-effort provenance extraction

    # ------------------------------------------------------------------
    # Write run.json early (incomplete — updated on finish)
    # ------------------------------------------------------------------
    started_at = utc_now_iso()
    run_json = {
        "run_id": run_id,
        "tool": "factory",
        "started_at_utc": started_at,
        "finished_at_utc": None,
        "verdict": None,
        "work_order_id": work_order.id,
        "version": get_tool_version(),
        "config": {
            "llm_model": args.llm_model,
            "llm_temperature": args.llm_temperature,
            "max_attempts": args.max_attempts,
            "timeout_seconds": args.timeout_seconds,
            "repo_root": repo_root,
        },
        "inputs": {
            "work_order_path": work_order_path,
            "work_order_sha256": sha256_file(work_order_path),
            "baseline_commit": baseline_commit,
        },
        "outputs": None,
        "planner_ref": planner_ref,
        "artifacts": {
            "run_summary": ARTIFACT_RUN_SUMMARY,
            "work_order_snapshot": ARTIFACT_WORK_ORDER,
        },
        "export": {
            "out_dir": export_dir,
        },
    }
    write_run_json(run_dir, run_json)

    run_config = {
        "llm_model": args.llm_model,
        "llm_temperature": args.llm_temperature,
        "max_attempts": args.max_attempts,
        "timeout_seconds": args.timeout_seconds,
        "repo_root": repo_root,
        "out_dir": run_dir,
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
    #       OR the work order is a bootstrap WO (creates the verify script)
    # ------------------------------------------------------------------
    wo_dict = work_order.model_dump()
    if wo_dict.get("verify_exempt") and not getattr(args, "allow_verify_exempt", False):
        # Auto-detect bootstrap: if postconditions create the verify script,
        # this WO is setting up the verification system itself. Forcing full
        # verification here is a deterministic failure — the thing being
        # verified doesn't exist until this WO creates it.
        postcond_paths = {
            c.get("path", "") for c in wo_dict.get("postconditions", [])
            if isinstance(c, dict)
        }
        is_bootstrap = _fd.VERIFY_SCRIPT_PATH in postcond_paths

        if is_bootstrap:
            con.step("M-22",
                "verify_exempt=true auto-honored — this work order creates "
                f"{_fd.VERIFY_SCRIPT_PATH} (bootstrap)")
        else:
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
    # Finalize run.json with outputs
    # ------------------------------------------------------------------
    run_json["finished_at_utc"] = utc_now_iso()
    run_json["verdict"] = verdict
    run_json["outputs"] = {
        "total_attempts": len(attempts),
        "repo_tree_hash_after": final_state.get("repo_tree_hash_after"),
        "run_summary_sha256": sha256_json(summary_dict),
    }
    write_run_json(run_dir, run_json)

    # ------------------------------------------------------------------
    # Optional export to user-specified out dir
    # ------------------------------------------------------------------
    if export_dir:
        import shutil
        export_run_dir = os.path.join(export_dir, run_id)
        if not os.path.exists(export_run_dir):
            shutil.copytree(run_dir, export_run_dir)

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
