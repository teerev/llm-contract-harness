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
    canonical_json_bytes,
    compute_run_id,
    is_path_inside_repo,
    load_json,
    make_attempt_dir,
    normalize_path,
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
