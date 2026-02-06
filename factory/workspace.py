"""Git helpers (is_git_repo, is_clean, baseline, rollback, tree hash).

This is NOT a temp-workspace copier — edits happen in-situ.
"""

from __future__ import annotations

import subprocess


def _git(args: list[str], cwd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a ``git`` sub-command, capturing output, with no shell."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        timeout=timeout,
        shell=False,
    )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def is_git_repo(repo_root: str) -> bool:
    """Return True if *repo_root* is inside a git working tree."""
    result = _git(["rev-parse", "--is-inside-work-tree"], cwd=repo_root)
    return result.returncode == 0 and result.stdout.strip() == b"true"


def is_clean(repo_root: str) -> bool:
    """Return True when there are no staged, unstaged, or untracked changes."""
    result = _git(["status", "--porcelain"], cwd=repo_root)
    if result.returncode != 0:
        return False
    return result.stdout.strip() == b""


def get_baseline_commit(repo_root: str) -> str:
    """Return the current HEAD commit hash."""
    result = _git(["rev-parse", "HEAD"], cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(
            f"git rev-parse HEAD failed: "
            f"{result.stderr.decode('utf-8', errors='replace')}"
        )
    return result.stdout.decode("utf-8").strip()


def get_tree_hash(repo_root: str, touched_files: list[str] | None = None) -> str:
    """Stage changes and return the tree-object hash (deterministic).

    If *touched_files* is provided (list of repo-relative paths), only those
    files are staged via ``git add --``.  Otherwise falls back to
    ``git add -A`` (stages everything).

    Scoping the add to *touched_files* prevents verification artifacts
    (e.g. ``__pycache__``, ``.pytest_cache``) from polluting the tree hash.
    """
    if touched_files:
        add = _git(["add", "--"] + sorted(touched_files), cwd=repo_root)
        add_desc = "git add -- <touched_files>"
    else:
        add = _git(["add", "-A"], cwd=repo_root)
        add_desc = "git add -A"
    if add.returncode != 0:
        raise RuntimeError(
            f"{add_desc} failed: {add.stderr.decode('utf-8', errors='replace')}"
        )
    result = _git(["write-tree"], cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(
            f"git write-tree failed: "
            f"{result.stderr.decode('utf-8', errors='replace')}"
        )
    return result.stdout.decode("utf-8").strip()


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def rollback(repo_root: str, baseline_commit: str) -> None:
    """Roll back to *baseline_commit*: ``git reset --hard`` + ``git clean -fdx``.

    Uses ``-fdx`` (not ``-fd``) so that files matching ``.gitignore`` patterns
    are also removed.  This is safe because the preflight guarantees a clean
    working tree before the run starts.
    """
    res = _git(["reset", "--hard", baseline_commit], cwd=repo_root)
    if res.returncode != 0:
        raise RuntimeError(
            f"git reset --hard failed: "
            f"{res.stderr.decode('utf-8', errors='replace')}"
        )
    res = _git(["clean", "-fdx"], cwd=repo_root)
    if res.returncode != 0:
        raise RuntimeError(
            f"git clean -fdx failed: "
            f"{res.stderr.decode('utf-8', errors='replace')}"
        )
