"""Tests for factory/schemas.py — Pydantic model validation."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from factory.schemas import (
    ALLOWED_STAGES,
    MAX_FILE_WRITE_BYTES,
    MAX_TOTAL_WRITE_BYTES,
    FailureBrief,
    FileWrite,
    WorkOrder,
    WriteProposal,
    load_work_order,
)


# ---------------------------------------------------------------------------
# WorkOrder validation
# ---------------------------------------------------------------------------


class TestWorkOrder:
    def _valid(self, **overrides):
        data = {
            "id": "wo1",
            "title": "T",
            "intent": "I",
            "allowed_files": ["src/a.py"],
            "forbidden": [],
            "acceptance_commands": ["echo ok"],
            "context_files": ["src/a.py"],
        }
        data.update(overrides)
        return WorkOrder(**data)

    def test_valid_construction(self):
        wo = self._valid()
        assert wo.id == "wo1"

    def test_absolute_path_rejected(self):
        with pytest.raises(ValidationError, match="must be relative"):
            self._valid(allowed_files=["/etc/passwd"])

    def test_drive_letter_rejected(self):
        with pytest.raises(ValidationError, match="drive letters"):
            self._valid(allowed_files=["C:foo.txt"])

    def test_dotdot_path_rejected(self):
        with pytest.raises(ValidationError, match="must not start with"):
            self._valid(allowed_files=["../../etc/passwd"])

    def test_empty_path_rejected(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            self._valid(allowed_files=[""])

    def test_empty_acceptance_commands_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            self._valid(acceptance_commands=[])

    def test_context_files_not_restricted_to_allowed(self):
        """context_files may include read-only upstream deps outside allowed_files."""
        wo = self._valid(
            allowed_files=["a.py"],
            context_files=["a.py", "b.py"],
        )
        assert wo.context_files == ["a.py", "b.py"]

    def test_context_files_max_10(self):
        files = [f"f{i}.py" for i in range(11)]
        with pytest.raises(ValidationError, match="at most 10"):
            self._valid(allowed_files=files, context_files=files)

    def test_path_normalization(self):
        wo = self._valid(
            allowed_files=["./src/a.py"],
            context_files=["./src/a.py"],
        )
        assert wo.allowed_files == ["src/a.py"]

    def test_notes_optional(self):
        wo = self._valid()
        assert wo.notes is None


# ---------------------------------------------------------------------------
# FileWrite / WriteProposal
# ---------------------------------------------------------------------------


class TestWriteProposal:
    def _write(self, **overrides):
        data = {"path": "a.py", "base_sha256": "abc123", "content": "x"}
        data.update(overrides)
        return data

    def test_valid_proposal(self):
        wp = WriteProposal(
            summary="test",
            writes=[FileWrite(**self._write())],
        )
        assert len(wp.writes) == 1

    def test_empty_writes_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            WriteProposal(summary="test", writes=[])

    def test_per_file_size_limit(self):
        big = "x" * (MAX_FILE_WRITE_BYTES + 1)
        with pytest.raises(ValidationError, match="exceeds"):
            WriteProposal(
                summary="test",
                writes=[FileWrite(**self._write(content=big))],
            )

    def test_total_size_limit(self):
        # 3 files each just under per-file limit but exceeding total
        size = MAX_FILE_WRITE_BYTES
        content = "x" * size
        writes = [
            FileWrite(**self._write(path=f"f{i}.py", content=content))
            for i in range(4)
        ]
        with pytest.raises(ValidationError, match="total write content exceeds"):
            WriteProposal(summary="test", writes=writes)

    def test_write_path_validated(self):
        with pytest.raises(ValidationError, match="must be relative"):
            FileWrite(path="/abs/path", base_sha256="abc", content="x")


# ---------------------------------------------------------------------------
# FailureBrief
# ---------------------------------------------------------------------------


class TestFailureBrief:
    def test_all_valid_stages(self):
        for stage in ALLOWED_STAGES:
            fb = FailureBrief(
                stage=stage,
                primary_error_excerpt="err",
                constraints_reminder="fix it",
            )
            assert fb.stage == stage

    def test_invalid_stage_rejected(self):
        with pytest.raises(ValidationError, match="stage must be one of"):
            FailureBrief(
                stage="bogus_stage",
                primary_error_excerpt="err",
                constraints_reminder="fix",
            )

    def test_optional_fields(self):
        fb = FailureBrief(
            stage="exception",
            primary_error_excerpt="err",
            constraints_reminder="fix",
        )
        assert fb.command is None
        assert fb.exit_code is None


# ---------------------------------------------------------------------------
# load_work_order
# ---------------------------------------------------------------------------


class TestLoadWorkOrder:
    def test_load_valid(self, tmp_path):
        p = str(tmp_path / "wo.json")
        data = {
            "id": "wo1",
            "title": "T",
            "intent": "I",
            "allowed_files": ["a.py"],
            "forbidden": [],
            "acceptance_commands": ["echo ok"],
            "context_files": ["a.py"],
        }
        with open(p, "w") as f:
            json.dump(data, f)
        wo = load_work_order(p)
        assert wo.id == "wo1"

    def test_load_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_work_order(str(tmp_path / "nope.json"))

    def test_load_invalid_json(self, tmp_path):
        p = str(tmp_path / "bad.json")
        with open(p, "w") as f:
            f.write("not json")
        with pytest.raises(json.JSONDecodeError):
            load_work_order(p)
