"""CLI entry logic — orchestrates the run, creates artifact dirs, writes run_summary."""

from __future__ import annotations

import json
import os
import sys
import traceback

from factory import defaults as _fd
from factory.console import Console
from factory.graph import build_graph
from factory.runtime import ensure_repo_venv, venv_env
from factory.schemas import WorkOrder, load_work_order
from factory.util import (
    ARTIFACT_FAILURE_BRIEF,
    ARTIFACT_RUN_SUMMARY,
    ARTIFACT_WORK_ORDER,
    _sandboxed_env,
    make_attempt_dir,
    save_json,
    sha256_file,
)
from factory.workspace import (
    clean_untracked,
    current_branch_name,
    ensure_git_identity,
    ensure_working_branch,
    get_baseline_commit,
    git_commit,
    git_push_branch,
    has_commits,
    is_clean,
    is_git_repo,
    resolve_commit,
    rollback,
)
from shared.run_context import (
    generate_ulid,
    get_tool_version,
    resolve_artifacts_root,
    sha256_json,
    utc_now_iso,
    write_run_json,
)


def _check_verify_exempt_policy(
    allow_flag: bool,
    provenance: dict | None,
) -> tuple[bool, str]:
    """Decide whether verify_exempt=True should be honored.

    Returns (allowed, reason).  If not allowed, the reason is an actionable
    error message for the operator.

    Policy:
    1. --allow-verify-exempt flag → always allow.
    2. Trusted planner bootstrap provenance → auto-allow with explanation.
    3. Otherwise → deny with instructions.
    """
    if allow_flag:
        return True, "verify_exempt=true honored (--allow-verify-exempt)"

    # Check for trusted planner bootstrap provenance.
    if (
        isinstance(provenance, dict)
        and provenance.get("bootstrap") is True
        and isinstance(provenance.get("planner_run_id"), str)
        and provenance["planner_run_id"]
    ):
        run_id = provenance["planner_run_id"]
        return True, (
            f"verify_exempt=true auto-honored — trusted planner bootstrap "
            f"(planner_run_id={run_id})"
        )

    return False, (
        "work order has verify_exempt=true but cannot be auto-honored.\n"
        "  To allow: pass --allow-verify-exempt\n"
        "  Auto-allow requires: provenance.bootstrap=true + provenance.planner_run_id"
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

    if not has_commits(repo_root):
        con.error(
            f"{repo_root} has no commits. The factory requires at least one "
            "commit to establish a baseline for rollback.\n"
            f"  Fix: cd {repo_root} && git add . && git commit -m 'init' --allow-empty"
        )
        sys.exit(1)

    ensure_git_identity(repo_root)

    # Record starting branch (reject detached HEAD)
    starting_branch = current_branch_name(repo_root)
    if starting_branch is None:
        con.error(
            f"{repo_root} is in detached HEAD state. "
            "The factory requires a named branch.\n"
            f"  Fix: cd {repo_root} && git checkout -b <branch-name>"
        )
        sys.exit(1)

    if not is_clean(repo_root):
        con.error(
            f"{repo_root} has uncommitted changes. "
            "The working tree must be clean (no staged, unstaged, or untracked changes)."
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # Ensure target-repo venv (runtime for verify/acceptance commands)
    # ------------------------------------------------------------------
    # H1–H3 verified: all verify/acceptance commands run via run_command()
    # in nodes_po.py with cwd=repo_root and env from _sandboxed_env().
    # We create a dedicated venv so 'python' and 'pytest' resolve there.
    python_override = getattr(args, "python", None)
    try:
        venv_root = ensure_repo_venv(
            repo_root,
            python=python_override,
        )
        command_env = venv_env(venv_root, _sandboxed_env())
        con.kv("Runtime", f"{venv_root}  (pytest installed)")
    except RuntimeError as exc:
        con.error(f"Failed to set up target-repo runtime:\n{exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Read provenance from work order (needed before branch naming + policy)
    # ------------------------------------------------------------------
    planner_ref = None
    planner_ref_raw: dict | None = None  # full provenance dict for policy checks
    planner_run_id_for_branch: str | None = None
    try:
        with open(work_order_path, "r", encoding="utf-8") as fh:
            wo_file_data = json.load(fh)
        prov = wo_file_data.get("provenance")
        if isinstance(prov, dict) and prov.get("planner_run_id"):
            planner_ref_raw = prov
            planner_ref = {
                "planner_run_id": prov.get("planner_run_id"),
                "compile_hash": prov.get("compile_hash"),
                "manifest_sha256": prov.get("manifest_sha256"),
            }
            planner_run_id_for_branch = prov.get("planner_run_id")
    except Exception:
        pass  # best-effort provenance extraction

    # ------------------------------------------------------------------
    # Resolve baseline commit (requested start-point for new branches)
    # ------------------------------------------------------------------
    commit_hash_arg = getattr(args, "commit_hash", None)
    if commit_hash_arg:
        try:
            baseline_commit_requested = resolve_commit(repo_root, commit_hash_arg)
        except ValueError as exc:
            con.error(str(exc))
            sys.exit(1)
        baseline_source = "commit-hash"
    else:
        baseline_commit_requested = get_baseline_commit(repo_root)
        baseline_source = "HEAD"

    # ------------------------------------------------------------------
    # Determine branch name (single point of branch selection)
    # ------------------------------------------------------------------
    run_id = generate_ulid()
    branch_arg = getattr(args, "branch", None)
    require_exists = getattr(args, "reuse_branch", False)
    require_new = getattr(args, "create_branch", False)

    if branch_arg:
        working_branch_name = branch_arg
        branch_mode = "explicit"
    else:
        # Auto-generate: factory/<planner_run_id>/<session> or factory/adhoc/<session>
        if planner_run_id_for_branch:
            working_branch_name = (
                f"{_fd.GIT_BRANCH_PREFIX}{planner_run_id_for_branch}/{run_id}"
            )
        else:
            working_branch_name = f"{_fd.GIT_BRANCH_PREFIX}adhoc/{run_id}"
        branch_mode = "auto"

    # Mainline protection
    if working_branch_name in _fd.GIT_PROTECTED_BRANCHES:
        con.error(
            f"Branch '{working_branch_name}' is protected. "
            "The factory will never commit directly to main/master.\n"
            f"  Use --branch <name> to specify a working branch."
        )
        sys.exit(1)

    try:
        branch_info = ensure_working_branch(
            repo_root,
            working_branch_name,
            baseline_commit_requested,
            require_exists=require_exists,
            require_new=require_new,
        )
    except (ValueError, RuntimeError) as exc:
        con.error(str(exc))
        sys.exit(1)

    factory_branch = branch_info["working_branch"]
    baseline_commit = branch_info["effective_baseline"]
    branch_existed = branch_info["branch_existed_at_start"]
    branch_created = branch_info["branch_created"]

    verb = "reused" if branch_existed else "created"
    con.step("git", f"branch {factory_branch} ({verb})",
             f"at {baseline_commit[:12]}")

    # ------------------------------------------------------------------
    # Canonical artifact directory
    # ------------------------------------------------------------------
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

    run_dir = os.path.join(artifacts_root, "factory", run_id)
    os.makedirs(run_dir, exist_ok=False)

    # For the graph state, out_dir points to the canonical factory parent
    # so that make_attempt_dir(out_dir, run_id, idx) produces the right path.
    out_dir = os.path.join(artifacts_root, "factory")

    # Persist the work order and CLI config for post-mortem reproducibility
    save_json(work_order.model_dump(), os.path.join(run_dir, ARTIFACT_WORK_ORDER))

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
            "baseline_commit_requested": baseline_commit_requested,
            "baseline_source": baseline_source,
        },
        "git_workflow": {
            "starting_branch": starting_branch,
            "working_branch": factory_branch,
            "branch_mode": branch_mode,
            "branch_existed_at_start": branch_existed,
            "baseline_commit_effective": baseline_commit,
            "commit_hashes_created": [],
            "push_attempted": False,
            "push_result": None,
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
    # Verify-exempt policy: auto-allow for trusted planner bootstrap WOs,
    # fail fast otherwise unless --allow-verify-exempt is passed.
    # ------------------------------------------------------------------
    wo_dict = work_order.model_dump()
    if wo_dict.get("verify_exempt"):
        allowed, reason = _check_verify_exempt_policy(
            allow_flag=getattr(args, "allow_verify_exempt", False),
            provenance=planner_ref_raw,
        )
        if allowed:
            con.step("policy", reason)
        else:
            con.error(reason)
            sys.exit(1)

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
        # Target-repo venv env for PO verify/acceptance subprocesses
        "command_env": command_env,
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
        "baseline_source": baseline_source,
        "working_branch": factory_branch,
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
    # Auto-commit on PASS (branch is already checked out — no branch creation here)
    # ------------------------------------------------------------------
    commit_hashes: list[str] = []
    if verdict == "PASS" and _fd.GIT_AUTO_COMMIT:
        # Extract touched_files from the successful attempt so we commit
        # only proposal-intended files, not verification artifacts.
        pass_touched: list[str] | None = None
        if attempts:
            last_attempt = attempts[-1]
            tf = last_attempt.get("touched_files", [])
            if tf:
                pass_touched = list(tf)
        try:
            commit_msg = f"{work_order.id}: applied by factory (run {run_id})"
            commit_sha = git_commit(repo_root, commit_msg, touched_files=pass_touched)
            commit_hashes.append(commit_sha)
            con.step("git", "commit", commit_sha[:12])
            # Clean verification artifacts (__pycache__, .pytest_cache, etc.)
            # left by the scoped commit so the repo is clean for the next WO.
            clean_untracked(repo_root)
        except RuntimeError as exc:
            con.warning(f"Auto-commit failed: {exc}")

    # ------------------------------------------------------------------
    # Push working branch (if enabled and commit succeeded)
    # ------------------------------------------------------------------
    no_push = getattr(args, "no_push", False)
    push_attempted = False
    push_result = None

    if commit_hashes and _fd.GIT_AUTO_PUSH and not no_push:
        push_attempted = True
        push_result = git_push_branch(repo_root, factory_branch)
        if push_result["ok"]:
            remote = push_result.get("remote", "?")
            con.step("git", f"push → {remote}/{factory_branch}")
        else:
            stderr_excerpt = (push_result.get("stderr") or "")[:300]
            con.warning(
                f"Push failed: {stderr_excerpt}\n"
                f"  Your changes are committed locally on branch '{factory_branch}'.\n"
                f"  Push manually: cd {repo_root} && git push -u origin {factory_branch}"
            )

    # Record final git workflow in run.json
    run_json["git_workflow"] = {
        "starting_branch": starting_branch,
        "working_branch": factory_branch,
        "branch_mode": branch_mode,
        "branch_existed_at_start": branch_existed,
        "baseline_commit_effective": baseline_commit,
        "commit_hashes_created": commit_hashes,
        "push_attempted": push_attempted,
        "push_result": push_result,
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

    # On FAIL/ERROR: print the last attempt's artifact paths so users can
    # find debugging files without navigating the artifact tree manually.
    if verdict != "PASS" and attempts:
        last = attempts[-1]
        last_idx = last.get("attempt_index", len(attempts))
        last_dir = make_attempt_dir(
            os.path.join(artifacts_root, "factory"), run_id, last_idx
        )
        con.kv("Last attempt", last_dir)

        # In verbose mode, also print the key debugging file paths.
        fb_path = os.path.join(last_dir, ARTIFACT_FAILURE_BRIEF)
        if os.path.isfile(fb_path):
            con.kv("Failure brief", fb_path, verbose_only=True)
        for vr in last.get("verify", []):
            for key in ("stdout_path", "stderr_path"):
                p = vr.get(key, "")
                if p and os.path.isfile(p):
                    con.kv(f"Verify {key.split('_')[0]}", p, verbose_only=True)

    if verdict != "PASS":
        sys.exit(1)
