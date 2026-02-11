"""Git helpers (is_git_repo, is_clean, baseline, rollback, tree hash, auto-commit).

This is NOT a temp-workspace copier — edits happen in-situ.
"""

from __future__ import annotations

import subprocess

from factory.defaults import (
    GIT_TIMEOUT_SECONDS,  # noqa: F401 — re-exported for backward compat
    GIT_USER_EMAIL,
    GIT_USER_NAME,
)


def _git(args: list[str], cwd: str, timeout: int = GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
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


# ---------------------------------------------------------------------------
# Git identity, pull, commit
# ---------------------------------------------------------------------------


def ensure_git_identity(repo_root: str) -> None:
    """Set local (repo-scoped) git user.name and user.email if not already set.

    Uses ``git config --local`` so this never touches the user's global config.
    Only writes if the repo does not already have a local identity configured.
    """
    name_result = _git(["config", "--local", "user.name"], cwd=repo_root)
    if name_result.returncode != 0 or not name_result.stdout.strip():
        _git(["config", "--local", "user.name", GIT_USER_NAME], cwd=repo_root)

    email_result = _git(["config", "--local", "user.email"], cwd=repo_root)
    if email_result.returncode != 0 or not email_result.stdout.strip():
        _git(["config", "--local", "user.email", GIT_USER_EMAIL], cwd=repo_root)


def git_pull(repo_root: str) -> str | None:
    """Run ``git pull --ff-only`` if a remote is configured. Returns output or None.

    Uses ``--ff-only`` to avoid creating merge commits. If no remote is
    configured, silently returns None. Raises on conflict or network error.
    """
    # Check if a remote exists
    remote_result = _git(["remote"], cwd=repo_root)
    if remote_result.returncode != 0 or not remote_result.stdout.strip():
        return None  # No remote configured — skip

    result = _git(["pull", "--ff-only"], cwd=repo_root, timeout=60)
    output = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git pull --ff-only failed: {stderr}")
    return output


def git_commit(repo_root: str, message: str) -> str:
    """Stage all changes and commit. Returns the new commit hash.

    Uses ``git add -A`` then ``git commit``. The ``--no-verify`` flag skips
    pre-commit hooks that might fail on LLM-generated code.
    """
    add = _git(["add", "-A"], cwd=repo_root)
    if add.returncode != 0:
        raise RuntimeError(
            f"git add -A failed: {add.stderr.decode('utf-8', errors='replace')}"
        )

    commit = _git(["commit", "--no-verify", "-m", message], cwd=repo_root)
    if commit.returncode != 0:
        stderr = commit.stderr.decode("utf-8", errors="replace").strip()
        # "nothing to commit" is not an error
        if "nothing to commit" in stderr or "nothing added to commit" in stderr:
            return get_baseline_commit(repo_root)
        raise RuntimeError(f"git commit failed: {stderr}")

    return get_baseline_commit(repo_root)


def git_push(repo_root: str) -> str | None:
    """Push to the default remote. Stub — not yet enabled.

    TODO: Enable when ready. Will need:
    - Check for remote existence
    - Handle authentication (SSH key, credential helper)
    - Handle push rejection (force-push policy)
    - Configurable remote name and branch
    """
    return None  # Not yet implemented
