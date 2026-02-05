from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.schemas import AttemptRecord, CmdResult, FailureBrief, WorkOrder, model_dump
from factory.util import cmd_to_string, run_command, shlex_split_posix, truncate_output, utc_now_iso, write_json
from factory.workspace import rollback_to_baseline


CONSTRAINTS_REMINDER_PO = (
    "Global verification must run before acceptance. Global verify is EXACTLY: "
    "if scripts/verify.sh exists run `bash scripts/verify.sh`, else run "
    "`python -m compileall -q .`, `python -m pip --version`, `python -m pytest -q`. "
    "Acceptance commands must be run via shlex.split() with no shell, and changes "
    "must be limited to allowed_files."
)


def global_verify_commands(repo_root: Path) -> list[list[str]]:
    verify_sh = repo_root / "scripts" / "verify.sh"
    if verify_sh.exists() and verify_sh.is_file():
        return [["bash", "scripts/verify.sh"]]
    return [
        ["python", "-m", "compileall", "-q", "."],
        ["python", "-m", "pip", "--version"],
        ["python", "-m", "pytest", "-q"],
    ]


def _results_payload(results: list[CmdResult]) -> list[dict[str, Any]]:
    return [model_dump(r) for r in results]


def _primary_excerpt(res: CmdResult) -> str:
    return truncate_output(res.stderr_trunc or res.stdout_trunc or "").strip() or "(no output)"


def po_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    LangGraph node: run global verification + acceptance commands.
    On FAIL, rollback to baseline.
    Always appends an AttemptRecord and writes verify/acceptance artifacts.
    """
    work_order: WorkOrder = state["work_order"]
    repo_root = Path(state["repo_root"])
    run_dir = Path(state["run_dir"])
    attempt_index = int(state["attempt_index"])
    max_attempts = int(state["max_attempts"])
    baseline_commit = str(state["baseline_commit"])
    timeout_seconds = int(state["timeout_seconds"])

    attempt_dir = run_dir / f"attempt_{attempt_index}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    verify_result_path = attempt_dir / "verify_result.json"
    acceptance_result_path = attempt_dir / "acceptance_result.json"
    failure_brief_path = attempt_dir / "failure_brief.json"

    patch_path = str(state.get("patch_path") or "")
    touched_files = list(state.get("touched_files") or [])
    apply_ok = bool(state.get("apply_ok") or False)

    verify_results: list[CmdResult] = []
    acceptance_results: list[CmdResult] = []
    fb: FailureBrief | None = state.get("failure_brief")

    verdict: str = "FAIL"
    ended_stage: str = fb.stage if fb is not None else "exception"

    try:
        if fb is not None or not apply_ok:
            # Earlier stage failed; skip verify/acceptance.
            if fb is None:
                fb = FailureBrief(
                    stage="patch_apply_failed",
                    command=None,
                    exit_code=None,
                    primary_error_excerpt="patch was not applied",
                    constraints_reminder=CONSTRAINTS_REMINDER_PO,
                )
                ended_stage = fb.stage
        else:
            # Global verify
            for i, cmd in enumerate(global_verify_commands(repo_root), start=1):
                res = run_command(
                    command=cmd,
                    cwd=repo_root,
                    timeout_seconds=timeout_seconds,
                    log_dir=attempt_dir,
                    log_name=f"verify_{i}",
                )
                verify_results.append(res)
                if res.exit_code != 0:
                    fb = FailureBrief(
                        stage="verify_failed",
                        command=cmd_to_string(cmd),
                        exit_code=res.exit_code,
                        primary_error_excerpt=_primary_excerpt(res),
                        constraints_reminder=CONSTRAINTS_REMINDER_PO,
                    )
                    ended_stage = fb.stage
                    break

            # Acceptance
            if fb is None:
                for i, cmd_str in enumerate(work_order.acceptance_commands, start=1):
                    argv = shlex_split_posix(cmd_str)
                    res = run_command(
                        command=argv,
                        cwd=repo_root,
                        timeout_seconds=timeout_seconds,
                        log_dir=attempt_dir,
                        log_name=f"acceptance_{i}",
                    )
                    acceptance_results.append(res)
                    if res.exit_code != 0:
                        fb = FailureBrief(
                            stage="acceptance_failed",
                            command=cmd_str,
                            exit_code=res.exit_code,
                            primary_error_excerpt=_primary_excerpt(res),
                            constraints_reminder=CONSTRAINTS_REMINDER_PO,
                        )
                        ended_stage = fb.stage
                        break

            if fb is None:
                verdict = "PASS"
                ended_stage = "success"
    except Exception as e:
        fb = FailureBrief(
            stage="exception",
            command=None,
            exit_code=None,
            primary_error_excerpt=truncate_output(str(e)).strip() or "(no output)",
            constraints_reminder=CONSTRAINTS_REMINDER_PO,
        )
        verdict = "FAIL"
        ended_stage = "exception"

    # Emit results artifacts (always).
    write_json(verify_result_path, _results_payload(verify_results))
    write_json(acceptance_result_path, _results_payload(acceptance_results))
    if fb is not None and verdict != "PASS":
        write_json(failure_brief_path, model_dump(fb))

    attempt_record = AttemptRecord(
        attempt_index=attempt_index,
        baseline_commit=baseline_commit,
        patch_path=patch_path,
        touched_files=touched_files,
        apply_ok=apply_ok,
        verify=verify_results,
        acceptance=acceptance_results,
        failure_brief=fb if verdict != "PASS" else None,
    )
    attempt_records = list(state.get("attempt_records") or [])
    attempt_records.append(attempt_record)

    # Rollback on FAIL (transaction semantics).
    if verdict != "PASS":
        rollback_to_baseline(
            repo_root=repo_root,
            baseline_commit=baseline_commit,
            timeout_seconds=timeout_seconds,
            log_dir=attempt_dir,
        )

    # Prepare next attempt routing.
    next_attempt_index = attempt_index
    if verdict != "PASS" and attempt_index < max_attempts:
        next_attempt_index = attempt_index + 1

    return {
        "attempt_records": attempt_records,
        "verdict": verdict,
        "ended_stage": ended_stage,
        "failure_brief": fb if verdict != "PASS" else None,
        "attempt_index": next_attempt_index,
        "po_completed_utc": utc_now_iso(),
    }

