"""Tests for factory/util.py — pure unit tests, no git, no subprocess."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from factory.util import (
    ARTIFACT_ACCEPTANCE_RESULT,
    ARTIFACT_FAILURE_BRIEF,
    ARTIFACT_PROPOSED_WRITES,
    ARTIFACT_RAW_LLM_RESPONSE,
    ARTIFACT_RUN_SUMMARY,
    ARTIFACT_SE_PROMPT,
    ARTIFACT_VERIFY_RESULT,
    ARTIFACT_WORK_ORDER,
    ARTIFACT_WRITE_RESULT,
    MAX_EXCERPT_CHARS,
    _sandboxed_env,
    canonical_json_bytes,
    compute_run_id,
    is_path_inside_repo,
    load_json,
    make_attempt_dir,
    normalize_path,
    run_command,
    save_json,
    sha256_bytes,
    sha256_file,
    split_command,
    truncate,
)


# ---------------------------------------------------------------------------
# truncate()
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_below_limit_unchanged(self):
        text = "a" * (MAX_EXCERPT_CHARS - 1)
        assert truncate(text) == text

    def test_at_limit_unchanged(self):
        text = "a" * MAX_EXCERPT_CHARS
        assert truncate(text) == text

    def test_above_limit_truncated(self):
        text = "a" * (MAX_EXCERPT_CHARS + 1)
        result = truncate(text)
        assert result.endswith("\n...[truncated]")
        assert len(result) == MAX_EXCERPT_CHARS + len("\n...[truncated]")

    def test_custom_limit(self):
        assert truncate("abcdef", max_chars=3) == "abc\n...[truncated]"

    def test_empty_string(self):
        assert truncate("") == ""

    def test_exact_custom_limit(self):
        assert truncate("abc", max_chars=3) == "abc"


# ---------------------------------------------------------------------------
# sha256_bytes / sha256_file
# ---------------------------------------------------------------------------


class TestHashing:
    def test_sha256_bytes_known(self):
        assert sha256_bytes(b"") == hashlib.sha256(b"").hexdigest()
        assert sha256_bytes(b"hello") == hashlib.sha256(b"hello").hexdigest()

    def test_sha256_file_existing(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_bytes(b"content")
        assert sha256_file(str(p)) == hashlib.sha256(b"content").hexdigest()

    def test_sha256_file_missing_returns_empty_hash(self, tmp_path):
        p = str(tmp_path / "nonexistent")
        assert sha256_file(p) == hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# canonical_json_bytes / compute_run_id
# ---------------------------------------------------------------------------


class TestCanonicalJson:
    def test_sorted_keys(self):
        result = canonical_json_bytes({"b": 1, "a": 2})
        assert json.loads(result) == {"a": 2, "b": 1}
        # Verify minimal separators — no spaces
        assert b" " not in result

    def test_deterministic(self):
        a = canonical_json_bytes({"x": [1, 2], "y": "z"})
        b = canonical_json_bytes({"y": "z", "x": [1, 2]})
        assert a == b


class TestComputeRunId:
    def test_length_16(self):
        rid = compute_run_id({"id": "test"}, "abc123")
        assert len(rid) == 16
        # Must be hex
        int(rid, 16)

    def test_deterministic(self):
        a = compute_run_id({"id": "x"}, "commit1")
        b = compute_run_id({"id": "x"}, "commit1")
        assert a == b

    def test_different_inputs_differ(self):
        a = compute_run_id({"id": "x"}, "commit1")
        b = compute_run_id({"id": "x"}, "commit2")
        assert a != b


# ---------------------------------------------------------------------------
# save_json / load_json
# ---------------------------------------------------------------------------


class TestJsonIO:
    def test_round_trip(self, tmp_path):
        p = str(tmp_path / "data.json")
        data = {"z": 1, "a": [2, 3]}
        save_json(data, p)
        loaded = load_json(p)
        assert loaded == data

    def test_sorted_keys(self, tmp_path):
        p = str(tmp_path / "data.json")
        save_json({"z": 1, "a": 2}, p)
        raw = open(p).read()
        assert raw.index('"a"') < raw.index('"z"')

    def test_trailing_newline(self, tmp_path):
        p = str(tmp_path / "data.json")
        save_json({"x": 1}, p)
        assert open(p).read().endswith("\n")

    def test_creates_parent_dirs(self, tmp_path):
        p = str(tmp_path / "a" / "b" / "c.json")
        save_json({"x": 1}, p)
        assert load_json(p) == {"x": 1}


# ---------------------------------------------------------------------------
# split_command
# ---------------------------------------------------------------------------


class TestSplitCommand:
    def test_simple(self):
        assert split_command("python -m pytest -q") == ["python", "-m", "pytest", "-q"]

    def test_quoted(self):
        assert split_command('echo "hello world"') == ["echo", "hello world"]

    def test_single_quotes(self):
        assert split_command("echo 'hello world'") == ["echo", "hello world"]


# ---------------------------------------------------------------------------
# normalize_path / is_path_inside_repo
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_normalize_removes_dot(self):
        assert normalize_path("./foo/bar") == "foo/bar"

    def test_normalize_collapses_double_dots(self):
        assert normalize_path("foo/../bar") == "bar"

    def test_is_inside_repo(self, tmp_path):
        repo = str(tmp_path / "repo")
        os.makedirs(repo)
        assert is_path_inside_repo("sub/file.txt", repo)

    def test_is_not_inside_repo(self, tmp_path):
        repo = str(tmp_path / "repo")
        os.makedirs(repo)
        assert not is_path_inside_repo("../../etc/passwd", repo)


# ---------------------------------------------------------------------------
# make_attempt_dir / ARTIFACT_* constants
# ---------------------------------------------------------------------------


class TestArtifactPaths:
    def test_make_attempt_dir_format(self):
        result = make_attempt_dir("/out", "run123", 1)
        assert result == os.path.join("/out", "run123", "attempt_1")

    def test_make_attempt_dir_sequential(self):
        dirs = [make_attempt_dir("/out", "r", i) for i in range(1, 4)]
        assert dirs == [
            os.path.join("/out", "r", "attempt_1"),
            os.path.join("/out", "r", "attempt_2"),
            os.path.join("/out", "r", "attempt_3"),
        ]

    def test_artifact_constants_are_strings(self):
        for name, val in [
            ("ARTIFACT_SE_PROMPT", ARTIFACT_SE_PROMPT),
            ("ARTIFACT_PROPOSED_WRITES", ARTIFACT_PROPOSED_WRITES),
            ("ARTIFACT_RAW_LLM_RESPONSE", ARTIFACT_RAW_LLM_RESPONSE),
            ("ARTIFACT_WRITE_RESULT", ARTIFACT_WRITE_RESULT),
            ("ARTIFACT_VERIFY_RESULT", ARTIFACT_VERIFY_RESULT),
            ("ARTIFACT_ACCEPTANCE_RESULT", ARTIFACT_ACCEPTANCE_RESULT),
            ("ARTIFACT_FAILURE_BRIEF", ARTIFACT_FAILURE_BRIEF),
            ("ARTIFACT_WORK_ORDER", ARTIFACT_WORK_ORDER),
            ("ARTIFACT_RUN_SUMMARY", ARTIFACT_RUN_SUMMARY),
        ]:
            assert isinstance(val, str), f"{name} should be a string"
            assert val.endswith((".json", ".txt")), f"{name} has unexpected extension: {val}"

    def test_artifact_constants_exact_values(self):
        """Pin the exact filenames to catch accidental renames."""
        assert ARTIFACT_SE_PROMPT == "se_prompt.txt"
        assert ARTIFACT_PROPOSED_WRITES == "proposed_writes.json"
        assert ARTIFACT_RAW_LLM_RESPONSE == "raw_llm_response.json"
        assert ARTIFACT_WRITE_RESULT == "write_result.json"
        assert ARTIFACT_VERIFY_RESULT == "verify_result.json"
        assert ARTIFACT_ACCEPTANCE_RESULT == "acceptance_result.json"
        assert ARTIFACT_FAILURE_BRIEF == "failure_brief.json"
        assert ARTIFACT_WORK_ORDER == "work_order.json"
        assert ARTIFACT_RUN_SUMMARY == "run_summary.json"


# ---------------------------------------------------------------------------
# save_json / load_json — M-05: atomic writes
# ---------------------------------------------------------------------------


class TestSaveJsonAtomic:
    """M-05: save_json must use tempfile + fsync + os.replace (atomic)."""

    def test_basic_write_and_load(self, tmp_path):
        """Baseline: save_json still works for normal use."""
        path = str(tmp_path / "data.json")
        save_json({"key": "value"}, path)
        loaded = load_json(path)
        assert loaded == {"key": "value"}

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "deep" / "nested" / "data.json")
        save_json({"x": 1}, path)
        assert load_json(path) == {"x": 1}

    def test_overwrites_existing(self, tmp_path):
        path = str(tmp_path / "data.json")
        save_json({"version": 1}, path)
        save_json({"version": 2}, path)
        assert load_json(path) == {"version": 2}

    def test_original_intact_on_replace_failure(self, tmp_path):
        """If os.replace fails, the original file must be unchanged."""
        path = str(tmp_path / "data.json")
        save_json({"original": True}, path)

        from unittest.mock import patch
        with patch("factory.util.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                save_json({"corrupted": True}, path)

        # Original file must be intact
        assert load_json(path) == {"original": True}

    def test_no_temp_files_left_on_failure(self, tmp_path):
        """On failure, the temp file must be cleaned up."""
        path = str(tmp_path / "data.json")
        save_json({"original": True}, path)

        from unittest.mock import patch
        with patch("factory.util.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                save_json({"bad": True}, path)

        # Only the original file should remain — no .tmp files
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "data.json"

    def test_sorted_keys_and_indent(self, tmp_path):
        """Output must be pretty-printed with sorted keys (backward compat)."""
        path = str(tmp_path / "data.json")
        save_json({"z": 1, "a": 2}, path)
        with open(path) as f:
            raw = f.read()
        # Sorted keys: "a" appears before "z"
        assert raw.index('"a"') < raw.index('"z"')
        # Indented (not compact)
        assert "\n" in raw
        # Trailing newline
        assert raw.endswith("\n")


# ---------------------------------------------------------------------------
# _sandboxed_env / run_command env — Issue 2 from GPT52ISSUES.md
# ---------------------------------------------------------------------------


class TestSandboxedEnv:
    """Issue 2: subprocess environment must suppress repo pollution."""

    def test_pythondontwritebytecode_set(self):
        env = _sandboxed_env()
        assert env.get("PYTHONDONTWRITEBYTECODE") == "1"

    def test_pytest_cache_suppressed(self):
        env = _sandboxed_env()
        addopts = env.get("PYTEST_ADDOPTS", "")
        assert "-p no:cacheprovider" in addopts

    def test_inherits_parent_env(self):
        env = _sandboxed_env()
        # PATH must be inherited for subprocesses to find executables
        assert "PATH" in env

    def test_run_command_passes_sandboxed_env(self, tmp_path):
        """run_command must pass env with PYTHONDONTWRITEBYTECODE to subprocess."""
        from unittest.mock import patch, MagicMock
        import subprocess

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b"ok"
        mock_proc.stderr = b""

        stdout_p = str(tmp_path / "out.txt")
        stderr_p = str(tmp_path / "err.txt")

        with patch("factory.util.subprocess.run", return_value=mock_proc) as mock_run:
            run_command(
                cmd=["echo", "hello"],
                cwd=str(tmp_path),
                timeout=10,
                stdout_path=stdout_p,
                stderr_path=stderr_p,
            )

        # Verify env kwarg was passed with the sandbox overrides
        call_kwargs = mock_run.call_args
        assert "env" in call_kwargs.kwargs or (
            len(call_kwargs.args) > 1 and isinstance(call_kwargs.args[1], dict)
        )
        passed_env = call_kwargs.kwargs.get("env") or call_kwargs.args[1]
        assert passed_env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert "-p no:cacheprovider" in passed_env.get("PYTEST_ADDOPTS", "")
