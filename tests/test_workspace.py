"""Tests for factory/workspace.py — real git repos in temp dirs."""

from __future__ import annotations

import os
import subprocess

import pytest

from factory.workspace import (
    GIT_TIMEOUT_SECONDS,
    get_baseline_commit,
    get_tree_hash,
    is_clean,
    is_git_repo,
    rollback,
)
from tests.conftest import init_git_repo


# ---------------------------------------------------------------------------
# is_git_repo
# ---------------------------------------------------------------------------


class TestIsGitRepo:
    def test_valid_repo(self, tmp_path):
        repo = init_git_repo(str(tmp_path / "repo"))
        assert is_git_repo(repo) is True

    def test_non_repo(self, tmp_path):
        d = str(tmp_path / "nope")
        os.makedirs(d)
        assert is_git_repo(d) is False


# ---------------------------------------------------------------------------
# is_clean
# ---------------------------------------------------------------------------


class TestIsClean:
    def test_clean_repo(self, git_repo):
        assert is_clean(git_repo) is True

    def test_untracked_file(self, git_repo):
        with open(os.path.join(git_repo, "new.txt"), "w") as f:
            f.write("new")
        assert is_clean(git_repo) is False

    def test_staged_change(self, git_repo):
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("changed")
        subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True)
        assert is_clean(git_repo) is False

    def test_unstaged_change(self, git_repo):
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("changed")
        assert is_clean(git_repo) is False


# ---------------------------------------------------------------------------
# get_baseline_commit
# ---------------------------------------------------------------------------


class TestGetBaselineCommit:
    def test_returns_hex_hash(self, git_repo):
        commit = get_baseline_commit(git_repo)
        assert len(commit) == 40
        int(commit, 16)  # must be valid hex

    def test_non_repo_raises(self, tmp_path):
        d = str(tmp_path / "nope")
        os.makedirs(d)
        with pytest.raises(RuntimeError, match="git rev-parse HEAD failed"):
            get_baseline_commit(d)


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


class TestRollback:
    def test_rollback_restores_file(self, git_repo):
        baseline = get_baseline_commit(git_repo)
        # Modify a file
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("modified")
        rollback(git_repo, baseline)
        with open(os.path.join(git_repo, "hello.txt")) as f:
            assert f.read() == "hello\n"

    def test_rollback_removes_untracked(self, git_repo):
        baseline = get_baseline_commit(git_repo)
        new_file = os.path.join(git_repo, "extra.txt")
        with open(new_file, "w") as f:
            f.write("extra")
        rollback(git_repo, baseline)
        assert not os.path.exists(new_file)

    def test_rollback_leaves_clean(self, git_repo):
        baseline = get_baseline_commit(git_repo)
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("dirty")
        with open(os.path.join(git_repo, "untracked.txt"), "w") as f:
            f.write("untracked")
        rollback(git_repo, baseline)
        assert is_clean(git_repo)


# ---------------------------------------------------------------------------
# get_tree_hash
# ---------------------------------------------------------------------------


class TestGetTreeHash:
    def test_returns_hex(self, git_repo):
        # Modify and stage
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("new content")
        h = get_tree_hash(git_repo, touched_files=["hello.txt"])
        assert len(h) == 40
        int(h, 16)

    def test_scoped_add(self, git_repo):
        """Scoped add should only stage specified files."""
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("changed")
        with open(os.path.join(git_repo, "other.txt"), "w") as f:
            f.write("also new")
        h = get_tree_hash(git_repo, touched_files=["hello.txt"])
        # Tree hash exists
        assert len(h) == 40


# ---------------------------------------------------------------------------
# GIT_TIMEOUT_SECONDS constant
# ---------------------------------------------------------------------------


def test_git_timeout_constant():
    assert GIT_TIMEOUT_SECONDS == 30
    assert isinstance(GIT_TIMEOUT_SECONDS, int)
