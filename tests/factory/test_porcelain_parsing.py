"""Tests for the NUL-delimited porcelain parser and is_clean robustness.

Covers:
  - Unit tests for _parse_porcelain_z with synthetic byte inputs
    (spaces in filenames, renames, copies, empty output, mixed entries)
  - Integration tests with real git repos for filenames with spaces
  - Verification that detect_repo_drift filters harness-managed dirs

These address H1/H2: the old newline-based parser was brittle for
filenames with spaces and ` -> ` sequences.  The new -z parser handles
these correctly by NUL-delimiting entries and avoiding ` -> ` splitting.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from factory.runtime import LLMCH_VENV_DIR
from factory.workspace import (
    _is_harness_managed,
    _parse_porcelain_z,
    detect_repo_drift,
    is_clean,
)
from tests.factory.conftest import init_git_repo


# ---------------------------------------------------------------------------
# Unit tests: _parse_porcelain_z with synthetic byte inputs
# ---------------------------------------------------------------------------


class TestParsePorcelainZ:
    """Unit tests for the NUL-delimited parser using raw byte inputs."""

    def test_empty_output(self):
        assert _parse_porcelain_z(b"") == []

    def test_single_untracked(self):
        # "?? hello.txt\0"
        raw = b"?? hello.txt\0"
        assert _parse_porcelain_z(raw) == ["hello.txt"]

    def test_multiple_entries(self):
        # "M  foo.py\0?? bar.txt\0"
        raw = b"M  foo.py\0?? bar.txt\0"
        assert _parse_porcelain_z(raw) == ["foo.py", "bar.txt"]

    def test_filename_with_spaces(self):
        """Filenames with spaces must be parsed correctly (no quoting in -z)."""
        raw = b"?? hello world.txt\0"
        paths = _parse_porcelain_z(raw)
        assert paths == ["hello world.txt"]

    def test_filename_with_arrow_sequence(self):
        """A filename containing ' -> ' must NOT be split on the arrow."""
        # This is the exact case that broke the old newline-based parser.
        raw = b"?? old -> new.txt\0"
        paths = _parse_porcelain_z(raw)
        assert paths == ["old -> new.txt"]

    def test_rename_entry(self):
        """Rename entries produce two NUL-terminated fields; only new path returned."""
        # "R  newname.txt\0oldname.txt\0"
        raw = b"R  newname.txt\0oldname.txt\0"
        paths = _parse_porcelain_z(raw)
        assert paths == ["newname.txt"]

    def test_copy_entry(self):
        """Copy entries also produce two fields; only destination returned."""
        raw = b"C  copy.txt\0original.txt\0"
        paths = _parse_porcelain_z(raw)
        assert paths == ["copy.txt"]

    def test_rename_plus_normal(self):
        """Mix of rename and normal entries parsed correctly."""
        # R  new.txt\0old.txt\0M  changed.txt\0?? extra.txt\0
        raw = b"R  new.txt\0old.txt\0M  changed.txt\0?? extra.txt\0"
        paths = _parse_porcelain_z(raw)
        assert paths == ["new.txt", "changed.txt", "extra.txt"]

    def test_staged_and_unstaged(self):
        """Staged (index) and unstaged (worktree) changes both appear."""
        raw = b"MM both.py\0A  added.py\0 D deleted.py\0"
        paths = _parse_porcelain_z(raw)
        assert "both.py" in paths
        assert "added.py" in paths
        assert "deleted.py" in paths

    def test_directory_with_trailing_slash(self):
        """Untracked dirs may have trailing slash; returned as-is by parser."""
        raw = b"?? somedir/\0"
        paths = _parse_porcelain_z(raw)
        # Parser returns raw path; callers strip the slash if needed.
        assert paths == ["somedir/"]

    def test_rename_with_spaces_in_both_paths(self):
        """Rename where both old and new paths contain spaces."""
        raw = b"R  new file.txt\0old file.txt\0"
        paths = _parse_porcelain_z(raw)
        assert paths == ["new file.txt"]


# ---------------------------------------------------------------------------
# Integration: is_clean with real git repos and tricky filenames
# ---------------------------------------------------------------------------


class TestIsCleanRobustFilenames:
    """Integration tests exercising real git + filenames with spaces."""

    def test_file_with_spaces_detected_as_dirty(self, tmp_path):
        """An untracked file with spaces in its name must make is_clean False."""
        repo = init_git_repo(str(tmp_path / "repo"))
        with open(os.path.join(repo, "hello world.txt"), "w") as f:
            f.write("content\n")
        assert not is_clean(repo)

    def test_file_with_spaces_only_in_venv_still_clean(self, tmp_path):
        """A file with spaces inside .llmch_venv/ must still be ignored."""
        repo = init_git_repo(str(tmp_path / "repo"))
        venv_sub = os.path.join(repo, LLMCH_VENV_DIR, "lib stuff")
        os.makedirs(venv_sub)
        with open(os.path.join(venv_sub, "some lib.py"), "w") as f:
            f.write("# lib\n")
        assert is_clean(repo)

    def test_renamed_file_detected_as_dirty(self, tmp_path):
        """A renamed tracked file must make is_clean False."""
        repo = init_git_repo(str(tmp_path / "repo"))
        # Rename hello.txt -> goodbye.txt via git
        subprocess.run(
            ["git", "mv", "hello.txt", "goodbye.txt"],
            cwd=repo, capture_output=True,
        )
        assert not is_clean(repo)


# ---------------------------------------------------------------------------
# detect_repo_drift: harness-managed paths filtered
# ---------------------------------------------------------------------------


class TestDetectRepoDriftFiltering:
    """Verify detect_repo_drift excludes harness-managed dirs."""

    def test_venv_not_reported_as_drift(self, tmp_path):
        """The .llmch_venv/ dir should not appear in drift results."""
        repo = init_git_repo(str(tmp_path / "repo"))

        # Create venv dir (harness infrastructure)
        venv_dir = os.path.join(repo, LLMCH_VENV_DIR)
        os.makedirs(venv_dir)
        with open(os.path.join(venv_dir, "pyvenv.cfg"), "w") as f:
            f.write("home = /usr/bin\n")

        # Modify the touched file
        with open(os.path.join(repo, "hello.txt"), "w") as f:
            f.write("changed\n")

        drift = detect_repo_drift(repo, ["hello.txt"])
        # Venv must not appear in drift
        assert not any(LLMCH_VENV_DIR in d for d in drift), (
            f".llmch_venv should not be in drift: {drift}"
        )

    def test_non_harness_untracked_still_detected(self, tmp_path):
        """Non-harness untracked files are still reported as drift."""
        repo = init_git_repo(str(tmp_path / "repo"))

        venv_dir = os.path.join(repo, LLMCH_VENV_DIR)
        os.makedirs(venv_dir)
        with open(os.path.join(repo, "rogue.txt"), "w") as f:
            f.write("unexpected\n")

        drift = detect_repo_drift(repo, [])
        assert "rogue.txt" in drift


# ---------------------------------------------------------------------------
# _is_harness_managed edge cases
# ---------------------------------------------------------------------------


class TestIsHarnessManaged:
    def test_exact_match(self):
        assert _is_harness_managed(LLMCH_VENV_DIR) is True

    def test_nested_path(self):
        assert _is_harness_managed(f"{LLMCH_VENV_DIR}/bin/python") is True

    def test_unrelated_path(self):
        assert _is_harness_managed("src/main.py") is False

    def test_prefix_but_not_subdir(self):
        """A path that starts with the venv name but isn't a subdir."""
        assert _is_harness_managed(f"{LLMCH_VENV_DIR}_extra") is False

    def test_empty_path(self):
        assert _is_harness_managed("") is False
