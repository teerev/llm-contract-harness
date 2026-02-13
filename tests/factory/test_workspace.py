"""Tests for factory/workspace.py — real git repos in temp dirs."""

from __future__ import annotations

import os
import subprocess

import pytest

from factory.workspace import (
    GIT_TIMEOUT_SECONDS,
    detect_repo_drift,
    get_baseline_commit,
    get_tree_hash,
    git_commit,
    is_clean,
    is_git_repo,
    rollback,
)
from tests.factory.conftest import init_git_repo


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
# detect_repo_drift — Issue 3 from GPT52ISSUES.md
# ---------------------------------------------------------------------------


class TestDetectRepoDrift:
    def test_no_drift_when_only_touched_files_changed(self, git_repo):
        """Modified touched files are not drift."""
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("changed")
        drift = detect_repo_drift(git_repo, ["hello.txt"])
        assert drift == []

    def test_untracked_file_detected_as_drift(self, git_repo):
        """An untracked file outside touched_files is drift."""
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("changed")
        with open(os.path.join(git_repo, "pollution.txt"), "w") as f:
            f.write("verification artifact")
        drift = detect_repo_drift(git_repo, ["hello.txt"])
        assert "pollution.txt" in drift

    def test_clean_repo_no_drift(self, git_repo):
        """A clean repo has no drift."""
        drift = detect_repo_drift(git_repo, ["hello.txt"])
        assert drift == []

    def test_multiple_drift_files(self, git_repo):
        """Multiple unexpected files are all reported."""
        with open(os.path.join(git_repo, "a.txt"), "w") as f:
            f.write("x")
        with open(os.path.join(git_repo, "b.txt"), "w") as f:
            f.write("y")
        drift = detect_repo_drift(git_repo, [])
        assert "a.txt" in drift
        assert "b.txt" in drift

    def test_pytest_cache_detected_as_drift(self, git_repo):
        """Verification artifacts like .pytest_cache/ appear as drift."""
        cache_dir = os.path.join(git_repo, ".pytest_cache")
        os.makedirs(cache_dir)
        with open(os.path.join(cache_dir, "README.md"), "w") as f:
            f.write("cache")
        drift = detect_repo_drift(git_repo, ["hello.txt"])
        assert any(".pytest_cache" in d for d in drift)


# ---------------------------------------------------------------------------
# git_commit — scoped staging (Issue 1 from GPT52ISSUES.md)
# ---------------------------------------------------------------------------


class TestGitCommit:
    def test_unscoped_commits_all(self, git_repo):
        """Without touched_files, git_commit stages everything (backward compat)."""
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("changed")
        with open(os.path.join(git_repo, "extra.txt"), "w") as f:
            f.write("new file")

        sha = git_commit(git_repo, "unscoped commit")
        assert len(sha) == 40

        # Both files should be in the commit
        result = subprocess.run(
            ["git", "show", "--stat", "--format=", "HEAD"],
            cwd=git_repo, capture_output=True, text=True,
        )
        assert "hello.txt" in result.stdout
        assert "extra.txt" in result.stdout

    def test_scoped_commits_only_touched_files(self, git_repo):
        """With touched_files, only those files are staged and committed."""
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("changed")
        with open(os.path.join(git_repo, "pollution.txt"), "w") as f:
            f.write("verification artifact")

        sha = git_commit(git_repo, "scoped commit", touched_files=["hello.txt"])
        assert len(sha) == 40

        # Only hello.txt should be in the commit
        result = subprocess.run(
            ["git", "show", "--stat", "--format=", "HEAD"],
            cwd=git_repo, capture_output=True, text=True,
        )
        assert "hello.txt" in result.stdout
        assert "pollution.txt" not in result.stdout

        # pollution.txt should still exist as untracked
        assert os.path.isfile(os.path.join(git_repo, "pollution.txt"))

    def test_scoped_commit_ignores_pytest_cache(self, git_repo):
        """Scoped commit does not include .pytest_cache/ even if present."""
        # Simulate verification artifacts
        cache_dir = os.path.join(git_repo, ".pytest_cache")
        os.makedirs(cache_dir)
        with open(os.path.join(cache_dir, "README.md"), "w") as f:
            f.write("pytest cache")
        with open(os.path.join(git_repo, "hello.txt"), "w") as f:
            f.write("changed")

        git_commit(git_repo, "scoped commit", touched_files=["hello.txt"])

        result = subprocess.run(
            ["git", "show", "--stat", "--format=", "HEAD"],
            cwd=git_repo, capture_output=True, text=True,
        )
        assert "hello.txt" in result.stdout
        assert ".pytest_cache" not in result.stdout

    def test_nothing_to_commit_returns_head(self, git_repo):
        """If touched_files haven't changed, returns current HEAD (no error)."""
        sha = git_commit(git_repo, "no-op", touched_files=["hello.txt"])
        assert len(sha) == 40  # returns HEAD hash, no exception


# ---------------------------------------------------------------------------
# GIT_TIMEOUT_SECONDS constant
# ---------------------------------------------------------------------------


def test_git_timeout_constant():
    assert GIT_TIMEOUT_SECONDS == 30
    assert isinstance(GIT_TIMEOUT_SECONDS, int)
