"""PO node — run global verification + acceptance commands, emit FailureBrief on failure."""

from __future__ import annotations

import os

from factory.schemas import CmdResult, FailureBrief, WorkOrder
from factory.util import run_command, save_json, split_command, truncate


# ---------------------------------------------------------------------------
# Global verification command rules (§6.5)
# ---------------------------------------------------------------------------


def _get_verify_commands(repo_root: str) -> list[list[str]]:
    """Return the global-verification command list.

    If ``scripts/verify.sh`` exists → ``bash scripts/verify.sh``.
    Otherwise, run the three fallback commands in order.
    """
    verify_script = os.path.join(repo_root, "scripts", "verify.sh")
    if os.path.isfile(verify_script):
        return [["bash", "scripts/verify.sh"]]
    return [
        ["python", "-m", "compileall", "-q", "."],
        ["python", "-m", "pip", "--version"],
        ["python", "-m", "pytest", "-q"],
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _combined_excerpt(cr: CmdResult) -> str:
    """Build a combined stderr+stdout excerpt from a failed command result.

    Reproduces the exact concat-then-strip pattern used previously inline:
    each present section adds ``[label]\\n<content>\\n``; the result is then
    ``.strip()``-ped so there is no leading/trailing whitespace.
    """
    parts: list[str] = []
    if cr.stderr_trunc:
        parts.append(f"[stderr]\n{cr.stderr_trunc}")
    if cr.stdout_trunc:
        parts.append(f"[stdout]\n{cr.stdout_trunc}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


def po_node(state: dict) -> dict:
    """PO node — run verify commands then acceptance commands."""
    work_order = WorkOrder(**state["work_order"])
    repo_root: str = state["repo_root"]
    timeout: int = state["timeout_seconds"]
    attempt_index: int = state["attempt_index"]
    run_id: str = state["run_id"]
    out_dir: str = state["out_dir"]

    attempt_dir = os.path.join(out_dir, run_id, f"attempt_{attempt_index}")
    os.makedirs(attempt_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Global verification
    # ------------------------------------------------------------------
    verify_cmds = _get_verify_commands(repo_root)
    verify_results: list[dict] = []

    for idx, cmd in enumerate(verify_cmds):
        cr = run_command(
            cmd=cmd,
            cwd=repo_root,
            timeout=timeout,
            stdout_path=os.path.join(attempt_dir, f"verify_{idx}_stdout.txt"),
            stderr_path=os.path.join(attempt_dir, f"verify_{idx}_stderr.txt"),
        )
        verify_results.append(cr.model_dump())

        if cr.exit_code != 0:
            fb = FailureBrief(
                stage="verify_failed",
                command=" ".join(cmd),
                exit_code=cr.exit_code,
                primary_error_excerpt=truncate(_combined_excerpt(cr)),
                constraints_reminder="Global verification must pass before acceptance.",
            )
            save_json(verify_results, os.path.join(attempt_dir, "verify_result.json"))
            return {
                "verify_results": verify_results,
                "acceptance_results": [],
                "failure_brief": fb.model_dump(),
            }

    save_json(verify_results, os.path.join(attempt_dir, "verify_result.json"))

    # ------------------------------------------------------------------
    # 2. Acceptance commands
    # ------------------------------------------------------------------
    acceptance_results: list[dict] = []

    for idx, cmd_str in enumerate(work_order.acceptance_commands):
        try:
            cmd = split_command(cmd_str)
        except ValueError as exc:
            fb = FailureBrief(
                stage="acceptance_failed",
                command=cmd_str,
                primary_error_excerpt=truncate(
                    f"Failed to parse acceptance command: {exc}"
                ),
                constraints_reminder=(
                    "Acceptance commands must be valid shell syntax "
                    "(parseable by shlex.split)."
                ),
            )
            save_json(
                acceptance_results,
                os.path.join(attempt_dir, "acceptance_result.json"),
            )
            return {
                "verify_results": verify_results,
                "acceptance_results": acceptance_results,
                "failure_brief": fb.model_dump(),
            }
        cr = run_command(
            cmd=cmd,
            cwd=repo_root,
            timeout=timeout,
            stdout_path=os.path.join(attempt_dir, f"acceptance_{idx}_stdout.txt"),
            stderr_path=os.path.join(attempt_dir, f"acceptance_{idx}_stderr.txt"),
        )
        acceptance_results.append(cr.model_dump())

        if cr.exit_code != 0:
            fb = FailureBrief(
                stage="acceptance_failed",
                command=cmd_str,
                exit_code=cr.exit_code,
                primary_error_excerpt=truncate(_combined_excerpt(cr)),
                constraints_reminder="All acceptance commands must exit 0.",
            )
            save_json(
                acceptance_results,
                os.path.join(attempt_dir, "acceptance_result.json"),
            )
            return {
                "verify_results": verify_results,
                "acceptance_results": acceptance_results,
                "failure_brief": fb.model_dump(),
            }

    save_json(
        acceptance_results, os.path.join(attempt_dir, "acceptance_result.json")
    )

    # All passed
    return {
        "verify_results": verify_results,
        "acceptance_results": acceptance_results,
        "failure_brief": None,
    }
