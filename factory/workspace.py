"""Git helpers (is_git_repo, is_clean, baseline, rollback, tree hash, branching, push).

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
# Repo drift detection (Issue 3 — post-PO cleanliness check)
# ---------------------------------------------------------------------------


def detect_repo_drift(repo_root: str, touched_files: list[str]) -> list[str]:
    """Return paths of modified/untracked files NOT in *touched_files*.

    Uses ``git status --porcelain`` to find all changes, then filters out
    the expected ``touched_files``.  Returns an empty list if the repo
    contains only the expected changes.
    """
    result = _git(["status", "--porcelain"], cwd=repo_root)
    if result.returncode != 0:
        return []  # can't detect — treat as clean

    expected = set(touched_files)
    drift: list[str] = []

    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        # porcelain format: "XY path" or "XY path -> renamed_path"
        if len(line) < 4:
            continue
        path = line[3:].strip()
        # Handle renames: "R  old -> new"
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path not in expected:
            drift.append(path)

    return sorted(drift)


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


def git_commit(
    repo_root: str,
    message: str,
    touched_files: list[str] | None = None,
) -> str:
    """Stage changes and commit. Returns the new commit hash.

    If *touched_files* is provided, stages only those files (scoped commit).
    Otherwise falls back to ``git add -A`` (stages everything).

    Scoped staging prevents verification artifacts (``.pytest_cache/``,
    ``__pycache__/``, etc.) from polluting commits.  The ``--no-verify``
    flag skips pre-commit hooks that might fail on LLM-generated code.
    """
    if touched_files:
        add = _git(["add", "--"] + sorted(touched_files), cwd=repo_root)
        add_desc = f"git add -- {sorted(touched_files)}"
    else:
        add = _git(["add", "-A"], cwd=repo_root)
        add_desc = "git add -A"
    if add.returncode != 0:
        raise RuntimeError(
            f"{add_desc} failed: {add.stderr.decode('utf-8', errors='replace')}"
        )

    commit = _git(["commit", "--no-verify", "-m", message], cwd=repo_root)
    if commit.returncode != 0:
        stderr = commit.stderr.decode("utf-8", errors="replace").strip()
        stdout = commit.stdout.decode("utf-8", errors="replace").strip()
        combined = f"{stderr} {stdout}"
        # "nothing to commit" is not an error — git may report this on
        # stdout or stderr depending on the staging method.
        if "nothing to commit" in combined or "nothing added to commit" in combined:
            return get_baseline_commit(repo_root)
        raise RuntimeError(f"git commit failed: {stderr or stdout}")

    return get_baseline_commit(repo_root)


# ---------------------------------------------------------------------------
# Branching
# ---------------------------------------------------------------------------


def has_commits(repo_root: str) -> bool:
    """Return True if the repo has at least one commit (HEAD is resolvable)."""
    result = _git(["rev-parse", "--verify", "HEAD"], cwd=repo_root)
    return result.returncode == 0


def resolve_commit(repo_root: str, commitish: str) -> str:
    """Resolve a commit-ish to a full 40-char SHA-1 hash.

    Raises ValueError if the commit-ish cannot be resolved.
    """
    result = _git(
        ["rev-parse", "--verify", commitish + "^{commit}"], cwd=repo_root
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(
            f"Cannot resolve '{commitish}' to a commit: {stderr}"
        )
    return result.stdout.decode("utf-8").strip()


def current_branch_name(repo_root: str) -> str | None:
    """Return the current branch name, or None if HEAD is detached."""
    result = _git(["symbolic-ref", "--short", "HEAD"], cwd=repo_root)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8").strip()


def branch_exists(repo_root: str, name: str) -> bool:
    """Return True if a local branch *name* exists."""
    result = _git(
        ["rev-parse", "--verify", f"refs/heads/{name}"], cwd=repo_root
    )
    return result.returncode == 0


def checkout_branch(repo_root: str, name: str) -> None:
    """Switch to an existing branch.

    Raises RuntimeError if the branch does not exist or checkout fails.
    """
    result = _git(["checkout", name], cwd=repo_root)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Failed to checkout branch '{name}': {stderr}")


def create_and_checkout_branch(
    repo_root: str, branch_name: str, start_point: str
) -> None:
    """Create and switch to a new branch starting at *start_point*.

    Raises RuntimeError if the branch already exists or checkout fails.
    """
    result = _git(
        ["checkout", "-b", branch_name, start_point], cwd=repo_root
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Failed to create branch '{branch_name}': {stderr}"
        )


def ensure_working_branch(
    repo_root: str,
    branch_name: str,
    start_point: str,
    *,
    require_exists: bool = False,
    require_new: bool = False,
) -> dict:
    """Ensure *branch_name* is checked out. Single entry point for branch setup.

    Modes (controlled by keyword flags):
    - ``require_exists=True``: branch MUST already exist (resume mode).
    - ``require_new=True``: branch must NOT exist (explicit create mode).
    - Both False (default): reuse if exists, create if not (auto mode).

    Returns a dict with audit fields::

        {
            "working_branch": str,
            "branch_existed_at_start": bool,
            "branch_created": bool,
            "effective_baseline": str,   # HEAD of branch after checkout
        }

    Raises ValueError on mode violations.
    """
    exists = branch_exists(repo_root, branch_name)

    if require_exists and not exists:
        raise ValueError(
            f"Branch '{branch_name}' does not exist "
            "(--reuse-branch requires an existing branch)."
        )

    if require_new and exists:
        raise ValueError(
            f"Branch '{branch_name}' already exists "
            "(--create-branch requires a new branch name)."
        )

    if exists:
        checkout_branch(repo_root, branch_name)
        effective_baseline = get_baseline_commit(repo_root)
        return {
            "working_branch": branch_name,
            "branch_existed_at_start": True,
            "branch_created": False,
            "effective_baseline": effective_baseline,
        }
    else:
        create_and_checkout_branch(repo_root, branch_name, start_point)
        effective_baseline = get_baseline_commit(repo_root)
        return {
            "working_branch": branch_name,
            "branch_existed_at_start": False,
            "branch_created": True,
            "effective_baseline": effective_baseline,
        }


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------


def git_push_branch(repo_root: str, branch_name: str) -> dict:
    """Push *branch_name* to the default remote with ``-u`` (set upstream).

    Returns a dict: ``{ok, remote, stdout, stderr}``.
    If no remote is configured, returns ``ok=False`` with an explanatory message.
    """
    # Discover first configured remote
    remote_result = _git(["remote"], cwd=repo_root)
    if remote_result.returncode != 0 or not remote_result.stdout.strip():
        return {
            "ok": False,
            "remote": None,
            "stdout": "",
            "stderr": "No remote configured. Run: git remote add origin <url>",
        }

    remote_name = remote_result.stdout.decode("utf-8").strip().splitlines()[0]

    result = _git(
        ["push", "-u", remote_name, branch_name],
        cwd=repo_root,
        timeout=60,
    )
    return {
        "ok": result.returncode == 0,
        "remote": remote_name,
        "stdout": result.stdout.decode("utf-8", errors="replace").strip(),
        "stderr": result.stderr.decode("utf-8", errors="replace").strip(),
    }
