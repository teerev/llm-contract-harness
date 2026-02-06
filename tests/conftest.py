"""Shared fixtures for the factory test suite."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# Git repo helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        timeout=30,
        shell=False,
    )


def init_git_repo(path: str, initial_file: str = "hello.txt", content: str = "hello\n") -> str:
    """Create a minimal git repo at *path* with one committed file.

    Returns the path (same as input, for convenience).
    """
    os.makedirs(path, exist_ok=True)
    _git(["init"], cwd=path)
    _git(["config", "user.email", "test@test.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    filepath = os.path.join(path, initial_file)
    with open(filepath, "w") as f:
        f.write(content)
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "init"], cwd=path)
    return path


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return sha256_of(f.read())


# ---------------------------------------------------------------------------
# Work order helpers
# ---------------------------------------------------------------------------


def minimal_work_order(**overrides: Any) -> dict:
    """Return a minimal valid work-order dict. Override any key."""
    wo = {
        "id": "test-wo-1",
        "title": "Test work order",
        "intent": "Test intent",
        "allowed_files": ["hello.txt"],
        "forbidden": [],
        "acceptance_commands": ["python -c 'print(1)'"],
        "context_files": ["hello.txt"],
        "notes": None,
    }
    wo.update(overrides)
    return wo


def write_work_order(path: str, **overrides: Any) -> str:
    """Write a minimal work-order JSON file and return its path."""
    wo = minimal_work_order(**overrides)
    with open(path, "w") as f:
        json.dump(wo, f)
    return path


# ---------------------------------------------------------------------------
# LLM stub helpers
# ---------------------------------------------------------------------------


def make_valid_proposal_json(repo_root: str, writes: list[dict] | None = None) -> str:
    """Build a valid LLM response JSON string for a proposal that writes hello.txt.

    If *writes* is provided, use those. Otherwise write ``hello.txt`` with correct hash.
    """
    if writes is None:
        fpath = os.path.join(repo_root, "hello.txt")
        if os.path.isfile(fpath):
            h = file_sha256(fpath)
        else:
            h = EMPTY_SHA256
        writes = [
            {
                "path": "hello.txt",
                "base_sha256": h,
                "content": "hello world\n",
            }
        ]
    proposal = {"summary": "test change", "writes": writes}
    return json.dumps(proposal)


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_repo(tmp_path):
    """A temporary git repo with one committed file (hello.txt)."""
    repo = str(tmp_path / "repo")
    init_git_repo(repo)
    return repo


@pytest.fixture()
def out_dir(tmp_path):
    """A temporary output directory (outside the git repo)."""
    d = str(tmp_path / "out")
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture()
def work_order_path(tmp_path):
    """Path to a minimal valid work-order JSON file."""
    p = str(tmp_path / "wo.json")
    write_work_order(p)
    return p
