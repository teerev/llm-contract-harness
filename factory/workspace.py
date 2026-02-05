from __future__ import annotations

from pathlib import Path

from factory.schemas import CmdResult
from factory.util import cmd_to_string, run_command, truncate_output


def git(
    *,
    repo_root: Path,
    args: list[str],
    timeout_seconds: int,
    log_dir: Path,
    log_name: str,
) -> CmdResult:
    return run_command(
        command=["git", *args],
        cwd=repo_root,
        timeout_seconds=timeout_seconds,
        log_dir=log_dir,
        log_name=log_name,
    )


def ensure_git_repo(*, repo_root: Path, timeout_seconds: int, log_dir: Path) -> None:
    res = git(
        repo_root=repo_root,
        args=["rev-parse", "--is-inside-work-tree"],
        timeout_seconds=timeout_seconds,
        log_dir=log_dir,
        log_name="preflight_git_rev_parse",
    )
    if res.exit_code != 0 or res.stdout_trunc.strip().lower() != "true":
        msg = truncate_output(res.stderr_trunc or res.stdout_trunc or "")
        raise ValueError(
            "repo_path is not a git repo (git rev-parse --is-inside-work-tree failed): "
            + (msg.strip() or "(no output)")
        )


def ensure_clean_working_tree(
    *, repo_root: Path, timeout_seconds: int, log_dir: Path
) -> None:
    res = git(
        repo_root=repo_root,
        args=["status", "--porcelain"],
        timeout_seconds=timeout_seconds,
        log_dir=log_dir,
        log_name="preflight_git_status_porcelain",
    )
    if res.exit_code != 0:
        raise ValueError(
            "failed to check git status: "
            + (truncate_output(res.stderr_trunc or res.stdout_trunc).strip() or "(no output)")
        )
    if res.stdout_trunc.strip() != "":
        raise ValueError(
            "working tree is not clean; refusing to run. "
            "Expected empty output from `git status --porcelain`, got:\n"
            + truncate_output(res.stdout_trunc).strip()
        )


def get_head_commit(*, repo_root: Path, timeout_seconds: int, log_dir: Path) -> str:
    res = git(
        repo_root=repo_root,
        args=["rev-parse", "HEAD"],
        timeout_seconds=timeout_seconds,
        log_dir=log_dir,
        log_name="preflight_git_rev_parse_head",
    )
    if res.exit_code != 0:
        raise ValueError(
            "failed to get baseline commit: "
            + (truncate_output(res.stderr_trunc or res.stdout_trunc).strip() or "(no output)")
        )
    return res.stdout_trunc.strip()


def rollback_to_baseline(
    *, repo_root: Path, baseline_commit: str, timeout_seconds: int, log_dir: Path
) -> list[CmdResult]:
    """
    Deterministic rollback:
    - git reset --hard <baseline_commit>
    - git clean -fd
    """
    results: list[CmdResult] = []
    results.append(
        git(
            repo_root=repo_root,
            args=["reset", "--hard", baseline_commit],
            timeout_seconds=timeout_seconds,
            log_dir=log_dir,
            log_name="rollback_git_reset_hard",
        )
    )
    results.append(
        git(
            repo_root=repo_root,
            args=["clean", "-fd"],
            timeout_seconds=timeout_seconds,
            log_dir=log_dir,
            log_name="rollback_git_clean_fd",
        )
    )
    return results


def cmdresult_to_failure_excerpt(res: CmdResult) -> str:
    """
    Best-effort bounded excerpt for failure messages.
    """
    return truncate_output(res.stderr_trunc or res.stdout_trunc or "").strip()


def format_cmd(res: CmdResult) -> str:
    return cmd_to_string(res.command)

