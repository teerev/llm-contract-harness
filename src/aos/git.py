"""
Git operations for AOS.

Provides:
- clone_repo(): Clone a GitHub repo to a local directory
- get_head_sha(): Get the current HEAD commit SHA
- push_branch(): Create and push a new branch (for writeback)
"""

import os
import subprocess
from pathlib import Path

from .validators import sanitize_error_message, SanitizedError


class GitError(Exception):
    """Git operation failed. Message is sanitized to remove tokens."""
    pass


def _run_git(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """
    Run a git command with error sanitization.
    
    Wraps subprocess.run to ensure any errors have tokens stripped
    before being raised.
    """
    try:
        return subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            **kwargs
        )
    except subprocess.CalledProcessError as e:
        # Sanitize both stdout and stderr before raising
        sanitized_stderr = sanitize_error_message(e.stderr or "")
        sanitized_stdout = sanitize_error_message(e.stdout or "")
        
        # Create a new error with sanitized messages
        raise GitError(
            f"Git command failed: {' '.join(args[:2])}...\n"
            f"stderr: {sanitized_stderr}\n"
            f"stdout: {sanitized_stdout}"
        ) from None  # Don't chain to avoid leaking original


def clone_repo(repo_url: str, target_dir: Path, ref: str = "main") -> str:
    """
    Clone a GitHub repository and checkout a specific ref.
    
    Args:
        repo_url: GitHub clone URL (https://github.com/org/repo.git)
        target_dir: Directory to clone into (must not exist)
        ref: Branch name or commit SHA to checkout
    
    Returns:
        The HEAD commit SHA after checkout
    
    Raises:
        GitError: If git commands fail (with sanitized error messages)
        ValueError: If target directory already exists
    """
    target_dir = Path(target_dir)
    
    if target_dir.exists():
        raise ValueError(f"Target directory already exists: {target_dir}")
    
    # Ensure parent exists
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    
    # Clone the repository
    # Use GITHUB_TOKEN if available for private repos
    clone_url = _inject_token(repo_url)
    
    _run_git(["git", "clone", clone_url, str(target_dir)])
    
    # Checkout the specified ref
    _run_git(["git", "checkout", ref], cwd=target_dir)
    
    return get_head_sha(target_dir)


def get_head_sha(repo_dir: Path) -> str:
    """Get the current HEAD commit SHA."""
    result = _run_git(["git", "rev-parse", "HEAD"], cwd=repo_dir)
    return result.stdout.strip()


def push_branch(
    repo_dir: Path,
    branch_name: str,
    commit_message: str,
    author_name: str = "AOS",
    author_email: str = "aos@localhost",
) -> str:
    """
    Commit all changes and push to a branch.
    
    If the branch doesn't exist, creates it. If it does exist,
    commits to it and pushes (handles sequential work orders to same branch).
    
    Args:
        repo_dir: Path to the git repository
        branch_name: Name for the branch
        commit_message: Commit message
        author_name: Git author name
        author_email: Git author email
    
    Returns:
        The pushed branch name
    
    Raises:
        GitError: If git commands fail (with sanitized error messages)
    """
    from .validators import validate_branch_name
    
    # Validate branch name before any git operations
    validate_branch_name(branch_name)
    
    repo_dir = Path(repo_dir)
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = author_name
    env["GIT_AUTHOR_EMAIL"] = author_email
    env["GIT_COMMITTER_NAME"] = author_name
    env["GIT_COMMITTER_EMAIL"] = author_email
    
    # Check current branch (non-checked, just to read)
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    current_branch = result.stdout.strip()
    
    # If not on the target branch, switch to it (create if needed)
    if current_branch != branch_name:
        # Check if branch exists locally (non-checked query)
        result = subprocess.run(
            ["git", "show-ref", "--verify", f"refs/heads/{branch_name}"],
            cwd=repo_dir,
            capture_output=True,
        )
        branch_exists = result.returncode == 0
        
        if branch_exists:
            # Checkout existing branch
            _run_git(["git", "checkout", branch_name], cwd=repo_dir)
        else:
            # Create new branch
            _run_git(["git", "checkout", "-b", branch_name], cwd=repo_dir)
    
    # Stage all changes
    _run_git(["git", "add", "-A"], cwd=repo_dir)
    
    # Check if there are changes to commit (non-checked query)
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_dir,
        capture_output=True,
    )
    has_changes = result.returncode != 0
    
    if has_changes:
        # Commit
        _run_git(
            ["git", "commit", "-m", commit_message],
            cwd=repo_dir,
            env=env,
        )
    
    # Push (force push to handle rebased/amended commits)
    _run_git(
        ["git", "push", "-u", "origin", branch_name, "--force-with-lease"],
        cwd=repo_dir,
    )
    
    return branch_name


def _inject_token(repo_url: str) -> str:
    """
    Inject GITHUB_TOKEN into the URL for authentication.
    
    Transforms: https://github.com/org/repo.git
    Into:       https://x-access-token:TOKEN@github.com/org/repo.git
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return repo_url
    
    if repo_url.startswith("https://github.com/"):
        return repo_url.replace(
            "https://github.com/",
            f"https://x-access-token:{token}@github.com/",
        )
    
    return repo_url
