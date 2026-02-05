from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from factory.graph import build_graph
from factory.llm import LLMClient
from factory.schemas import FailureBrief, RunSummary, load_work_order, write_pretty_json
from factory.util import (
    config_hash,
    ensure_outside_repo,
    run_id as compute_run_id,
    stable_repo_tree_hash,
    truncate_output,
    utc_now_iso,
    work_order_hash_from_path,
)
from factory.workspace import ensure_clean_working_tree, ensure_git_repo, get_head_commit, rollback_to_baseline


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m factory run")
    p.add_argument("--repo", required=True, help="Path to product repo (git repo)")
    p.add_argument("--work-order", required=True, help="Path to work order JSON")
    p.add_argument("--out", required=True, help="Output directory for artifacts")
    p.add_argument("--max-attempts", type=int, default=2)
    p.add_argument("--llm-model", required=True)
    p.add_argument("--llm-temperature", type=float, default=0.0)
    p.add_argument("--timeout-seconds", type=int, default=600)
    return p.parse_args(argv)


def run_cli(argv: list[str] | None = None) -> int:
    started_utc = utc_now_iso()
    try:
        args = _parse_args(argv)
    except SystemExit as e:
        # argparse help / error
        code = e.code if isinstance(e.code, int) else 1
        return int(code)

    repo_root = Path(args.repo).expanduser().resolve()
    work_order_path = Path(args.work_order).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()

    try:
        if not repo_root.exists() or not repo_root.is_dir():
            raise ValueError(f"--repo must be an existing directory: {repo_root}")
        if not work_order_path.exists() or not work_order_path.is_file():
            raise ValueError(f"--work-order must be an existing file: {work_order_path}")

        out_dir.mkdir(parents=True, exist_ok=True)
        ensure_outside_repo(repo_root, out_dir)

        # Load work order (schema validation).
        work_order = load_work_order(work_order_path)

        # Preflight git + clean-tree checks.
        preflight_log_dir = out_dir / "_preflight_logs"
        preflight_log_dir.mkdir(parents=True, exist_ok=True)
        ensure_git_repo(repo_root=repo_root, timeout_seconds=int(args.timeout_seconds), log_dir=preflight_log_dir)
        ensure_clean_working_tree(
            repo_root=repo_root, timeout_seconds=int(args.timeout_seconds), log_dir=preflight_log_dir
        )
        baseline_commit = get_head_commit(
            repo_root=repo_root, timeout_seconds=int(args.timeout_seconds), log_dir=preflight_log_dir
        )

        repo_tree_hash_before = stable_repo_tree_hash(repo_root, out_dir=out_dir)
        work_order_hash = work_order_hash_from_path(work_order_path)
        cfg_hash = config_hash(
            args.llm_model, float(args.llm_temperature), int(args.max_attempts), int(args.timeout_seconds)
        )
        rid = compute_run_id(work_order_hash, repo_tree_hash_before, cfg_hash)

        run_dir = out_dir / rid
        run_dir.mkdir(parents=True, exist_ok=True)

        # Best-effort cleanup of preflight logs to keep artifacts minimal.
        try:
            for p in sorted(preflight_log_dir.glob("*")):
                p.unlink(missing_ok=True)  # type: ignore[arg-type]
            preflight_log_dir.rmdir()
        except Exception:
            pass

        llm = LLMClient(model=str(args.llm_model), temperature=float(args.llm_temperature))

        graph = build_graph()
        initial_state: dict[str, Any] = {
            "repo_root": str(repo_root),
            "out_dir": str(out_dir),
            "run_dir": str(run_dir),
            "run_id": rid,
            "work_order": work_order,
            "work_order_path": str(work_order_path),
            "attempt_index": 1,
            "max_attempts": int(args.max_attempts),
            "baseline_commit": baseline_commit,
            "timeout_seconds": int(args.timeout_seconds),
            "llm": llm,
            "patch_proposal": None,
            "patch_path": "",
            "touched_files": [],
            "apply_ok": False,
            "failure_brief": None,
            "attempt_records": [],
        }

        final_state: dict[str, Any] = graph.invoke(initial_state)  # type: ignore[assignment]

        verdict = str(final_state.get("verdict") or "FAIL")

        if verdict == "PASS":
          # Remove untracked artifacts created by verify/acceptance (e.g. __pycache__/).
            from factory.workspace import clean_untracked
            clean_untracked(
                repo_root=repo_root,
                timeout_seconds=int(args.timeout_seconds),
                log_dir=(run_dir / "attempt_1"),
            )

        ended_stage = str(final_state.get("ended_stage") or "exception")
        attempt_records = list(final_state.get("attempt_records") or [])

        repo_tree_hash_after = stable_repo_tree_hash(repo_root, out_dir=out_dir)
        ended_utc = utc_now_iso()

        summary = RunSummary(
            run_id=rid,
            repo_path=str(repo_root),
            work_order_path=str(work_order_path),
            work_order_hash=work_order_hash,
            repo_baseline_commit=baseline_commit,
            repo_tree_hash_before=repo_tree_hash_before,
            repo_tree_hash_after=repo_tree_hash_after,
            max_attempts=int(args.max_attempts),
            attempts=attempt_records,
            verdict="PASS" if verdict == "PASS" else "FAIL",
            ended_stage=ended_stage,
            started_utc=started_utc,
            ended_utc=ended_utc,
        )

        summary_path = run_dir / "run_summary.json"
        write_pretty_json(summary_path, summary)
        print(f"{summary.verdict} {summary_path}")
        return 0 if summary.verdict == "PASS" else 1

    except Exception as e:
        # Best-effort rollback if we have baseline_commit.
        try:
            baseline_commit = locals().get("baseline_commit")
            if isinstance(baseline_commit, str) and baseline_commit:
                # Use attempt_1 directory if it exists, else out_dir.
                log_dir = (locals().get("run_dir") or out_dir)  # type: ignore[assignment]
                log_dir_p = Path(str(log_dir))
                (log_dir_p / "attempt_1").mkdir(parents=True, exist_ok=True)
                rollback_to_baseline(
                    repo_root=repo_root,
                    baseline_commit=baseline_commit,
                    timeout_seconds=int(args.timeout_seconds),
                    log_dir=(log_dir_p / "attempt_1"),
                )
        except Exception:
            pass

        msg = truncate_output(str(e)).strip() or "(no output)"
        fb = FailureBrief(
            stage="preflight",
            command=None,
            exit_code=None,
            primary_error_excerpt=msg,
            constraints_reminder="Preflight failed (git-only + clean working tree + valid paths required).",
        )
        print(truncate_output(f"FAIL {fb.stage}: {fb.primary_error_excerpt}"), file=sys.stderr)
        return 1

