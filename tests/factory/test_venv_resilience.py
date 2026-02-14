"""Tests proving .llmch_venv resilience across preflight, rollback, and clean.

These tests exercise real git commands (no mocking) to verify that the
harness-managed venv directory survives the operations that previously
destroyed it.

Confirmed hypotheses addressed:
  H1: is_clean() must not reject a repo that only has .llmch_venv/ untracked.
      Evidence: workspace.py::is_clean used git status --porcelain with no
      filtering; .llmch_venv/ shows as "?? .llmch_venv/" → is_clean=False.
  H2: rollback() and clean_untracked() must preserve .llmch_venv/.
      Evidence: workspace.py::rollback/clean_untracked ran git clean -fdx
      with no -e excludes; this deletes all untracked dirs including the venv.
  H3: Stale marker (marker exists, python binary missing) must trigger rebuild.
      Evidence: runtime.py::ensure_repo_venv checked marker + python existence
      but if only the marker survived (unlikely now, but possible with
      partial disk writes), it would return the broken venv.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from factory.runtime import LLMCH_VENV_DIR, _MARKER_FILE, ensure_repo_venv
from factory.workspace import clean_untracked, is_clean, rollback

from tests.factory.conftest import init_git_repo


# ---------------------------------------------------------------------------
# T1 — Clean-tree allowlist: .llmch_venv/ is ignored, other dirt is caught
# ---------------------------------------------------------------------------


class TestCleanTreeAllowlist:
    """H1 + H5: is_clean must ignore .llmch_venv but still catch real changes."""

    def test_repo_with_only_llmch_venv_is_clean(self, tmp_path):
        """A repo whose only untracked content is .llmch_venv/ is clean."""
        repo = init_git_repo(str(tmp_path / "repo"))

        # Simulate the venv directory existing (as it would between WO runs)
        venv_dir = os.path.join(repo, LLMCH_VENV_DIR)
        os.makedirs(venv_dir)
        # Put some content inside to make git report it
        with open(os.path.join(venv_dir, "pyvenv.cfg"), "w") as f:
            f.write("home = /usr/bin\n")

        assert is_clean(repo), (
            "is_clean should return True when only .llmch_venv/ is untracked"
        )

    def test_repo_with_other_untracked_file_is_dirty(self, tmp_path):
        """A repo with a non-harness untracked file is NOT clean."""
        repo = init_git_repo(str(tmp_path / "repo"))

        # Create harness dir (allowed) AND a rogue file (should fail)
        venv_dir = os.path.join(repo, LLMCH_VENV_DIR)
        os.makedirs(venv_dir)
        with open(os.path.join(repo, "junk.txt"), "w") as f:
            f.write("unexpected file\n")

        assert not is_clean(repo), (
            "is_clean should return False when non-harness untracked files exist"
        )

    def test_repo_with_staged_change_is_dirty(self, tmp_path):
        """Staged changes are always caught, even with .llmch_venv/ present."""
        repo = init_git_repo(str(tmp_path / "repo"))

        venv_dir = os.path.join(repo, LLMCH_VENV_DIR)
        os.makedirs(venv_dir)

        # Stage a modification
        with open(os.path.join(repo, "hello.txt"), "w") as f:
            f.write("modified\n")
        subprocess.run(["git", "add", "hello.txt"], cwd=repo, capture_output=True)

        assert not is_clean(repo), (
            "is_clean should return False when staged changes exist"
        )

    def test_empty_repo_is_clean(self, tmp_path):
        """Baseline: a fresh repo with no untracked content is clean."""
        repo = init_git_repo(str(tmp_path / "repo"))
        assert is_clean(repo)

    def test_nested_venv_file_is_ignored(self, tmp_path):
        """Deep paths inside .llmch_venv/ are also ignored."""
        repo = init_git_repo(str(tmp_path / "repo"))

        deep = os.path.join(repo, LLMCH_VENV_DIR, "lib", "python3.12", "site-packages")
        os.makedirs(deep)
        with open(os.path.join(deep, "pytest.py"), "w") as f:
            f.write("# fake\n")

        assert is_clean(repo)


# ---------------------------------------------------------------------------
# T2 — Rollback preserves venv
# ---------------------------------------------------------------------------


class TestRollbackPreservesVenv:
    """H2: rollback() and clean_untracked() must not delete .llmch_venv/."""

    def test_rollback_preserves_venv_dir(self, tmp_path):
        """After rollback, .llmch_venv/ and its contents still exist."""
        repo = init_git_repo(str(tmp_path / "repo"))
        baseline = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True
        ).stdout.decode().strip()

        # Create the venv dir with a sentinel file
        venv_dir = os.path.join(repo, LLMCH_VENV_DIR)
        os.makedirs(venv_dir)
        sentinel = os.path.join(venv_dir, "sentinel.txt")
        with open(sentinel, "w") as f:
            f.write("must survive rollback\n")

        # Also create a non-harness untracked file (should be removed)
        with open(os.path.join(repo, "artifact.txt"), "w") as f:
            f.write("should be deleted\n")

        rollback(repo, baseline)

        # Venv and sentinel must survive
        assert os.path.isdir(venv_dir), ".llmch_venv/ was deleted by rollback"
        assert os.path.isfile(sentinel), "sentinel inside .llmch_venv/ was deleted"

        # Non-harness artifact must be gone
        assert not os.path.exists(os.path.join(repo, "artifact.txt")), (
            "Non-harness artifact should have been removed by rollback"
        )

    def test_clean_untracked_preserves_venv_dir(self, tmp_path):
        """After clean_untracked, .llmch_venv/ and its contents still exist."""
        repo = init_git_repo(str(tmp_path / "repo"))

        # Create the venv dir with a sentinel
        venv_dir = os.path.join(repo, LLMCH_VENV_DIR)
        os.makedirs(venv_dir)
        sentinel = os.path.join(venv_dir, "sentinel.txt")
        with open(sentinel, "w") as f:
            f.write("must survive clean\n")

        # Create a pytest cache (typical verification artifact, should be removed)
        cache_dir = os.path.join(repo, ".pytest_cache")
        os.makedirs(cache_dir)
        with open(os.path.join(cache_dir, "README.md"), "w") as f:
            f.write("cache\n")

        clean_untracked(repo)

        # Venv survives
        assert os.path.isdir(venv_dir), ".llmch_venv/ was deleted by clean_untracked"
        assert os.path.isfile(sentinel), "sentinel inside .llmch_venv/ was deleted"

        # Pytest cache removed
        assert not os.path.exists(cache_dir), (
            ".pytest_cache/ should have been removed by clean_untracked"
        )

    def test_rollback_still_removes_modified_tracked_files(self, tmp_path):
        """Rollback still restores tracked files to baseline state."""
        repo = init_git_repo(str(tmp_path / "repo"))
        baseline = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True
        ).stdout.decode().strip()

        # Modify a tracked file
        with open(os.path.join(repo, "hello.txt"), "w") as f:
            f.write("modified after baseline\n")

        # Create venv
        venv_dir = os.path.join(repo, LLMCH_VENV_DIR)
        os.makedirs(venv_dir)

        rollback(repo, baseline)

        # Tracked file restored
        with open(os.path.join(repo, "hello.txt")) as f:
            assert f.read() == "hello\n"

        # Venv preserved
        assert os.path.isdir(venv_dir)


# ---------------------------------------------------------------------------
# T3 — Marker corruption detection
# ---------------------------------------------------------------------------


class TestMarkerCorruption:
    """H3: ensure_repo_venv must detect and rebuild when marker is stale."""

    def test_marker_without_python_triggers_rebuild(self, tmp_path):
        """If .llmch_ok exists but bin/python is missing, rebuild the venv."""
        repo = str(tmp_path / "repo")
        os.makedirs(repo)

        # Create a venv dir with only the marker (simulating corruption)
        venv_dir = Path(repo) / LLMCH_VENV_DIR
        venv_dir.mkdir()
        (venv_dir / _MARKER_FILE).write_text("ok\n")
        # No bin/python exists — this is the corruption

        # ensure_repo_venv should detect this and rebuild
        result = ensure_repo_venv(repo, install_pytest=False)

        assert result.is_dir()
        # After rebuild, the python binary should exist
        if os.name == "nt":
            python_path = result / "Scripts" / "python.exe"
        else:
            python_path = result / "bin" / "python"
        assert python_path.is_file(), (
            f"After rebuild, python should exist at {python_path}"
        )
        # Marker should be re-written
        assert (result / _MARKER_FILE).is_file()

    def test_healthy_venv_is_not_rebuilt(self, tmp_path):
        """A healthy venv (marker + python present) is returned immediately."""
        repo = str(tmp_path / "repo")
        os.makedirs(repo)

        # Create a real venv first
        venv_root = ensure_repo_venv(repo, install_pytest=False)
        marker_mtime = (venv_root / _MARKER_FILE).stat().st_mtime

        # Call again — should be a fast no-op (marker not rewritten)
        import time
        time.sleep(0.05)  # small delay to detect mtime change
        venv_root_2 = ensure_repo_venv(repo, install_pytest=False)

        assert venv_root == venv_root_2
        marker_mtime_2 = (venv_root_2 / _MARKER_FILE).stat().st_mtime
        assert marker_mtime == marker_mtime_2, (
            "Marker was rewritten even though venv was healthy"
        )
