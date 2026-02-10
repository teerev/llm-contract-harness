"""Tests for factory/schemas.py — Pydantic model validation."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from factory.schemas import (
    ALLOWED_STAGES,
    MAX_FILE_WRITE_BYTES,
    MAX_TOTAL_WRITE_BYTES,
    Condition,
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

    # --- M-07: ".", NUL, and control char rejection ---

    def test_dot_path_rejected(self):
        with pytest.raises(ValidationError, match="must not be '.'"):
            self._valid(allowed_files=["."])

    def test_dotslash_path_rejected(self):
        """'.' is caught after normpath: './' normalizes to '.'."""
        with pytest.raises(ValidationError, match="must not be '.'"):
            self._valid(allowed_files=["./"])

    def test_nul_byte_rejected(self):
        with pytest.raises(ValidationError, match="NUL"):
            self._valid(allowed_files=["src/a\x00b.py"])

    def test_control_char_rejected(self):
        with pytest.raises(ValidationError, match="control character"):
            self._valid(allowed_files=["src/a\x01b.py"])

    def test_tab_in_path_rejected(self):
        with pytest.raises(ValidationError, match="control character"):
            self._valid(allowed_files=["src/a\tb.py"])

    def test_normal_path_still_passes(self):
        """Sanity: normal paths are unaffected by the new checks."""
        wo = self._valid(allowed_files=["src/main.py"])
        assert wo.allowed_files == ["src/main.py"]

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

    def test_backward_compatible_no_conditions(self):
        """Old-format WO dict (no preconditions/postconditions/verify_exempt) parses."""
        wo = self._valid()
        assert wo.preconditions == []
        assert wo.postconditions == []
        assert wo.verify_exempt is False

    def test_with_preconditions(self):
        wo = self._valid(
            preconditions=[{"kind": "file_exists", "path": "src/dep.py"}],
        )
        assert len(wo.preconditions) == 1
        assert wo.preconditions[0].kind == "file_exists"
        assert wo.preconditions[0].path == "src/dep.py"

    def test_with_postconditions(self):
        wo = self._valid(
            postconditions=[{"kind": "file_exists", "path": "src/a.py"}],
        )
        assert len(wo.postconditions) == 1
        assert wo.postconditions[0].kind == "file_exists"

    def test_verify_exempt_default_false(self):
        wo = self._valid()
        assert wo.verify_exempt is False

    def test_verify_exempt_set_true(self):
        wo = self._valid(verify_exempt=True)
        assert wo.verify_exempt is True

    def test_postcondition_file_absent_rejected(self):
        """Postconditions may only use file_exists, not file_absent."""
        with pytest.raises(ValidationError, match="file_exists"):
            self._valid(
                postconditions=[{"kind": "file_absent", "path": "src/a.py"}],
            )

    def test_postcondition_mixed_rejected(self):
        """Even one file_absent among valid postconditions is rejected."""
        with pytest.raises(ValidationError, match="file_exists"):
            self._valid(
                postconditions=[
                    {"kind": "file_exists", "path": "src/a.py"},
                    {"kind": "file_absent", "path": "src/b.py"},
                ],
            )


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------


class TestCondition:
    def test_file_exists_valid(self):
        c = Condition(kind="file_exists", path="src/a.py")
        assert c.kind == "file_exists"
        assert c.path == "src/a.py"

    def test_file_absent_valid(self):
        c = Condition(kind="file_absent", path="src/b.py")
        assert c.kind == "file_absent"
        assert c.path == "src/b.py"

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValidationError):
            Condition(kind="file_modified", path="src/a.py")  # type: ignore[arg-type]

    def test_absolute_path_rejected(self):
        with pytest.raises(ValidationError, match="must be relative"):
            Condition(kind="file_exists", path="/etc/passwd")

    def test_empty_path_rejected(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            Condition(kind="file_exists", path="")

    def test_dotdot_path_rejected(self):
        with pytest.raises(ValidationError, match="must not start with"):
            Condition(kind="file_exists", path="../../etc/passwd")

    def test_path_normalization(self):
        c = Condition(kind="file_exists", path="./src/a.py")
        assert c.path == "src/a.py"

    def test_drive_letter_rejected(self):
        with pytest.raises(ValidationError, match="drive letters"):
            Condition(kind="file_exists", path="C:foo.txt")


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

    def test_load_old_format_gets_defaults(self, tmp_path):
        """Old-format JSON (no conditions, no verify_exempt) loads with defaults."""
        p = str(tmp_path / "wo_old.json")
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
        assert wo.preconditions == []
        assert wo.postconditions == []
        assert wo.verify_exempt is False

    def test_load_with_conditions(self, tmp_path):
        """JSON with conditions round-trips correctly."""
        p = str(tmp_path / "wo_new.json")
        data = {
            "id": "wo1",
            "title": "T",
            "intent": "I",
            "preconditions": [{"kind": "file_exists", "path": "scripts/verify.sh"}],
            "postconditions": [{"kind": "file_exists", "path": "a.py"}],
            "allowed_files": ["a.py"],
            "forbidden": [],
            "acceptance_commands": ["echo ok"],
            "context_files": ["a.py"],
            "verify_exempt": True,
        }
        with open(p, "w") as f:
            json.dump(data, f)
        wo = load_work_order(p)
        assert len(wo.preconditions) == 1
        assert wo.preconditions[0].kind == "file_exists"
        assert wo.preconditions[0].path == "scripts/verify.sh"
        assert len(wo.postconditions) == 1
        assert wo.postconditions[0].path == "a.py"
        assert wo.verify_exempt is True

    def test_load_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_work_order(str(tmp_path / "nope.json"))

    def test_load_invalid_json(self, tmp_path):
        p = str(tmp_path / "bad.json")
        with open(p, "w") as f:
            f.write("not json")
        with pytest.raises(json.JSONDecodeError):
            load_work_order(p)
