"""Tests for planner/validation.py — structured validation errors and all rules."""

from __future__ import annotations

import pytest

from planner.validation import (
    E000_STRUCTURAL,
    E001_ID,
    E003_SHELL_OP,
    E004_GLOB,
    E005_SCHEMA,
    E006_SYNTAX,
    E007_SHLEX,
    SHELL_OPERATOR_TOKENS,
    ValidationError,
    _check_python_c_syntax,
    normalize_work_order,
    parse_and_validate,
    validate_plan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wo(wo_id: str = "WO-01", **overrides) -> dict:
    """Build a minimal valid work order dict.

    Does NOT include ``bash scripts/verify.sh`` in acceptance — the factory
    handles global verify automatically, and including it is now banned (R7).
    """
    base = {
        "id": wo_id,
        "title": f"Test {wo_id}",
        "intent": "test intent",
        "allowed_files": ["src/a.py"],
        "forbidden": [],
        "acceptance_commands": ['python -c "assert True"'],
        "context_files": ["src/a.py"],
        "notes": None,
    }
    base.update(overrides)
    return base


def _codes(errors: list[ValidationError]) -> set[str]:
    """Extract the set of error codes from a list of ValidationError."""
    return {e.code for e in errors}


def _codes_for_wo(errors: list[ValidationError], wo_id: str) -> set[str]:
    """Extract error codes for a specific work order."""
    return {e.code for e in errors if e.wo_id == wo_id}


# ---------------------------------------------------------------------------
# ValidationError basics
# ---------------------------------------------------------------------------


class TestValidationError:
    def test_str_with_wo_id(self):
        e = ValidationError(code="E001", wo_id="WO-03", message="bad id")
        s = str(e)
        # Assert structural properties rather than exact format
        assert "E001" in s
        assert "WO-03" in s
        assert "bad id" in s

    def test_str_without_wo_id(self):
        e = ValidationError(code="E000", wo_id=None, message="empty list")
        s = str(e)
        assert "E000" in s
        assert "empty list" in s

    def test_to_dict(self):
        e = ValidationError(
            code="E003", wo_id="WO-02", message="shell op", field="acceptance_commands"
        )
        d = e.to_dict()
        assert d == {
            "code": "E003",
            "wo_id": "WO-02",
            "message": "shell op",
            "field": "acceptance_commands",
        }

    def test_frozen(self):
        e = ValidationError(code="E001", wo_id=None, message="x")
        with pytest.raises(AttributeError):
            e.code = "E002"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# E000: Structural errors
# ---------------------------------------------------------------------------


class TestE000Structural:
    def test_empty_work_orders_list(self):
        errors = validate_plan([])
        assert len(errors) == 1
        assert errors[0].code == E000_STRUCTURAL
        assert "empty" in errors[0].message

    def test_top_level_not_dict(self):
        _, errors = parse_and_validate("not a dict")  # type: ignore[arg-type]
        assert len(errors) == 1
        assert errors[0].code == E000_STRUCTURAL
        assert "object" in errors[0].message

    def test_missing_work_orders_key(self):
        _, errors = parse_and_validate({"system_overview": []})
        assert len(errors) == 1
        assert errors[0].code == E000_STRUCTURAL
        assert "work_orders" in errors[0].message

    # --- M-03: Non-dict elements in work_orders ---

    def test_non_dict_int_element_via_validate_plan(self):
        """validate_plan must not crash on non-dict elements — returns E000."""
        errors = validate_plan([42, _wo("WO-01")])
        assert any(e.code == E000_STRUCTURAL for e in errors)
        assert any("int" in e.message for e in errors)

    def test_non_dict_string_element_via_validate_plan(self):
        errors = validate_plan(["hello"])
        assert any(e.code == E000_STRUCTURAL for e in errors)
        assert any("str" in e.message for e in errors)

    def test_non_dict_list_element_via_validate_plan(self):
        errors = validate_plan([[1, 2, 3]])
        assert any(e.code == E000_STRUCTURAL for e in errors)
        assert any("list" in e.message for e in errors)

    def test_non_dict_mixed_via_parse_and_validate(self):
        """parse_and_validate must not crash on non-dict elements."""
        wos, errors = parse_and_validate({
            "work_orders": [42, "x", _wo("WO-01")],
        })
        # Should return errors, not raise AttributeError
        assert len(errors) >= 2  # at least one per non-dict element
        assert all(e.code == E000_STRUCTURAL for e in errors)
        # No normalized work orders returned when non-dict elements present
        assert wos == []

    def test_all_non_dict_elements_via_parse_and_validate(self):
        """All elements are non-dict — structured errors, no crash."""
        wos, errors = parse_and_validate({
            "work_orders": [42, None, True],
        })
        assert len(errors) == 3
        assert all(e.code == E000_STRUCTURAL for e in errors)
        assert wos == []


# ---------------------------------------------------------------------------
# E001: ID format and contiguity
# ---------------------------------------------------------------------------


class TestE001Id:
    def test_valid_single_id(self):
        errors = validate_plan([_wo("WO-01")])
        assert E001_ID not in _codes(errors)

    def test_valid_contiguous_ids(self):
        errors = validate_plan([
            _wo("WO-01"),
            _wo("WO-02"),
            _wo("WO-03"),
        ])
        assert E001_ID not in _codes(errors)

    def test_bad_format(self):
        errors = validate_plan([_wo("wo-1")])
        assert E001_ID in _codes(errors)

    def test_non_contiguous(self):
        errors = validate_plan([
            _wo("WO-01"),
            _wo("WO-03"),  # gap: skips WO-02
        ])
        assert E001_ID in _codes_for_wo(errors, "WO-03")

    def test_wrong_start(self):
        errors = validate_plan([_wo("WO-02")])
        assert E001_ID in _codes(errors)


# ---------------------------------------------------------------------------
# E002: Verify command in acceptance (REMOVED — rule was inverted to R7/E105)
# ---------------------------------------------------------------------------


class TestE002VerifyRemoved:
    def test_acceptance_without_verify_passes(self):
        """After M4, acceptance_commands without 'bash scripts/verify.sh' is valid."""
        wo = _wo("WO-02", acceptance_commands=['python -c "assert True"'])
        errors = validate_plan([_wo("WO-01"), wo])
        # E002 should never appear — the rule no longer exists.
        assert all(e.code != "E002" for e in errors)

    def test_wo01_bootstrap_no_special_case(self):
        """WO-01 creating verify.sh no longer needs a bootstrap exemption."""
        wo = _wo(
            "WO-01",
            allowed_files=["scripts/verify.sh"],
            context_files=["scripts/verify.sh"],
            acceptance_commands=['python -c "assert True"'],
        )
        errors = validate_plan([wo])
        assert all(e.code != "E002" for e in errors)


# ---------------------------------------------------------------------------
# E003: Shell operators
# ---------------------------------------------------------------------------


class TestE003ShellOp:
    def test_clean_command_passes(self):
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "x = 1; print(x)"',  # semicolon inside quotes is OK
        ])
        errors = validate_plan([wo])
        assert E003_SHELL_OP not in _codes(errors)

    @pytest.mark.parametrize("op", sorted(SHELL_OPERATOR_TOKENS))
    def test_all_shell_operators_rejected(self, op):
        """Every token in SHELL_OPERATOR_TOKENS must be caught."""
        wo = _wo("WO-01", acceptance_commands=[f"echo foo {op} echo bar"])
        errors = validate_plan([wo])
        assert E003_SHELL_OP in _codes(errors)

    def test_operator_inside_quotes_safe(self):
        """Shell operators inside quoted strings are not bare tokens."""
        wo = _wo("WO-01", acceptance_commands=[
            'python -c "a = 1; b = a | 2; print(b)"',  # bitwise OR inside quotes
        ])
        errors = validate_plan([wo])
        assert E003_SHELL_OP not in _codes(errors)

    def test_shlex_parse_error_emits_e007(self):
        """M-04: Commands with unmatched quotes now produce E007 (not silent skip)."""
        wo = _wo("WO-01", acceptance_commands=["echo 'unterminated"])
        errors = validate_plan([wo])
        assert E007_SHLEX in _codes(errors)
        assert E003_SHELL_OP not in _codes(errors)  # E003 not reached — E007 replaces it


# ---------------------------------------------------------------------------
# E007: Unparseable acceptance commands (M-04)
# ---------------------------------------------------------------------------


class TestE007Shlex:
    """M-04: shlex.split failure must produce E007, not silently pass."""

    def test_unmatched_single_quote(self):
        wo = _wo("WO-01", acceptance_commands=["echo 'unterminated"])
        errors = validate_plan([wo])
        assert E007_SHLEX in _codes(errors)

    def test_unmatched_double_quote(self):
        wo = _wo("WO-01", acceptance_commands=['echo "unterminated'])
        errors = validate_plan([wo])
        assert E007_SHLEX in _codes(errors)

    def test_valid_command_no_e007(self):
        wo = _wo("WO-01", acceptance_commands=['python -c "print(1)"'])
        errors = validate_plan([wo])
        assert E007_SHLEX not in _codes(errors)

    def test_multiple_bad_commands_multiple_e007(self):
        """Each unparseable command gets its own E007."""
        wo = _wo("WO-01", acceptance_commands=[
            "echo 'a",
            "echo \"b",
        ])
        errors = validate_plan([wo])
        e007s = [e for e in errors if e.code == E007_SHLEX]
        # At least 2 E007 errors (one from E003 loop, one from _check_python_c_syntax
        # per command — but the E003 loop and python-c check may both fire for the same
        # command). The important thing: no silent skip.
        assert len(e007s) >= 2

    def test_e007_via_parse_and_validate(self):
        """Full pipeline: parse_and_validate catches shlex errors."""
        _, errors = parse_and_validate({
            "work_orders": [
                _wo("WO-01", acceptance_commands=["echo 'bad"]),
            ],
        })
        assert E007_SHLEX in _codes(errors)

    def test_e007_message_contains_command(self):
        wo = _wo("WO-01", acceptance_commands=["echo 'unterminated"])
        errors = validate_plan([wo])
        e007s = [e for e in errors if e.code == E007_SHLEX]
        assert any("unterminated" in e.message for e in e007s)


# ---------------------------------------------------------------------------
# E004: Glob characters
# ---------------------------------------------------------------------------


class TestE004Glob:
    def test_clean_paths_pass(self):
        wo = _wo("WO-01", allowed_files=["src/a.py"], context_files=["src/a.py"])
        errors = validate_plan([wo])
        assert E004_GLOB not in _codes(errors)

    def test_glob_star_in_allowed_files(self):
        wo = _wo("WO-01", allowed_files=["src/*.py"], context_files=["src/a.py"])
        errors = validate_plan([wo])
        e004s = [e for e in errors if e.code == E004_GLOB]
        assert len(e004s) >= 1
        assert "allowed_files" in e004s[0].message

    def test_glob_question_in_context_files(self):
        wo = _wo("WO-01", context_files=["src/?.py"])
        errors = validate_plan([wo])
        e004s = [e for e in errors if e.code == E004_GLOB]
        assert len(e004s) >= 1
        assert "context_files" in e004s[0].message

    def test_glob_bracket_rejected(self):
        wo = _wo("WO-01", allowed_files=["src/[ab].py"])
        errors = validate_plan([wo])
        assert E004_GLOB in _codes(errors)


# ---------------------------------------------------------------------------
# E005: Schema validation
# ---------------------------------------------------------------------------


class TestE005Schema:
    def test_valid_schema_passes(self):
        wo = _wo("WO-01")
        errors = validate_plan([wo])
        assert E005_SCHEMA not in _codes(errors)

    def test_absolute_path_rejected(self):
        wo = _wo("WO-01", allowed_files=["/etc/passwd"])
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)

    def test_path_traversal_rejected(self):
        wo = _wo("WO-01", allowed_files=["../../../etc/passwd"])
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)

    def test_path_traversal_in_middle_rejected(self):
        """Normalized path like src/../../../etc/shadow starts with '..'."""
        wo = _wo("WO-01", allowed_files=["src/../../../etc/shadow"])
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)

    def test_windows_drive_letter_rejected(self):
        wo = _wo("WO-01", allowed_files=["C:\\Windows\\System32\\cmd.exe"])
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)

    def test_empty_path_rejected(self):
        wo = _wo("WO-01", allowed_files=[""])
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)

    def test_empty_acceptance_rejected(self):
        wo = _wo("WO-01", acceptance_commands=[])
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)

    def test_too_many_context_files(self):
        files = [f"f{i}.py" for i in range(11)]
        wo = _wo("WO-01", allowed_files=files, context_files=files)
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)

    def test_postcondition_file_absent_rejected(self):
        """Postconditions may only use file_exists (factory cannot delete)."""
        wo = _wo("WO-01", postconditions=[
            {"kind": "file_absent", "path": "src/a.py"},
        ])
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)


# ---------------------------------------------------------------------------
# E006: Python syntax in python -c
# ---------------------------------------------------------------------------


class TestE006Syntax:
    def test_valid_python_c_passes(self):
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "x = 1; print(x)"',
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX not in _codes(errors)

    def test_syntax_error_caught(self):
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "def foo(:"',  # SyntaxError
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX in _codes(errors)

    def test_multiline_python_c(self):
        # Valid multi-statement python -c
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "import os; assert os.path.isfile(\'x.py\')"',
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX not in _codes(errors)

    def test_non_python_command_not_checked(self):
        """bash commands are not syntax-checked."""
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            "bash scripts/run.sh",
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX not in _codes(errors)

    def test_python_without_c_flag_not_checked(self):
        """python script.py is not syntax-checked (only python -c)."""
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            "python run_tests.py",
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX not in _codes(errors)

    def test_helper_returns_none_for_valid(self):
        assert _check_python_c_syntax('python -c "1+1"', "WO-01") is None

    def test_helper_returns_error_for_invalid(self):
        err = _check_python_c_syntax('python -c "if True"', "WO-01")
        assert err is not None
        assert err.code == E006_SYNTAX
        assert err.wo_id == "WO-01"

    def test_helper_returns_none_for_non_python(self):
        assert _check_python_c_syntax("bash foo.sh", "WO-01") is None

    def test_helper_returns_e007_on_shlex_error(self):
        """M-04: Unmatched quotes → shlex.split ValueError → returns E007."""
        err = _check_python_c_syntax("python -c 'print(1", "WO-01")
        assert err is not None
        assert err.code == E007_SHLEX
        assert "shlex" in err.message.lower()

    def test_incomplete_expression(self):
        """Incomplete expression like 'def' alone is a syntax error."""
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "def"',
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX in _codes(errors)


# ---------------------------------------------------------------------------
# parse_and_validate integration
# ---------------------------------------------------------------------------


class TestParseAndValidate:
    def test_valid_manifest(self):
        manifest = {
            "system_overview": ["test"],
            "work_orders": [_wo("WO-01")],
        }
        work_orders, errors = parse_and_validate(manifest)
        assert E000_STRUCTURAL not in _codes(errors)
        assert len(work_orders) == 1

    def test_returns_normalized_work_orders(self):
        wo = _wo("WO-01", allowed_files=["  src/a.py  "])
        manifest = {"work_orders": [wo]}
        work_orders, _ = parse_and_validate(manifest)
        # Whitespace should be stripped
        assert work_orders[0]["allowed_files"] == ["src/a.py"]

    def test_multiple_errors_accumulated(self):
        """A plan with multiple problems returns all errors, not just the first."""
        wo1 = _wo("WO-01", acceptance_commands=[
            'python -c "def foo(:"',  # E006 — syntax error
        ])
        wo2 = _wo(
            "WO-03",  # gap → E001 (should be WO-02)
            acceptance_commands=['python -c "assert True"'],
        )
        _, errors = parse_and_validate({"work_orders": [wo1, wo2]})
        codes = _codes(errors)
        assert E001_ID in codes      # WO-03 should be WO-02
        assert E006_SYNTAX in codes  # WO-01 has syntax error


# ---------------------------------------------------------------------------
# normalize_work_order
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_strips_whitespace(self):
        raw = {"id": "  WO-01 ", "title": " T ", "allowed_files": [" a.py "]}
        result = normalize_work_order(raw)
        assert result["id"] == "WO-01"
        assert result["title"] == "T"
        assert result["allowed_files"] == ["a.py"]

    def test_deduplicates(self):
        raw = {"allowed_files": ["a.py", "a.py", "b.py"]}
        result = normalize_work_order(raw)
        assert result["allowed_files"] == ["a.py", "b.py"]

    def test_deduplicates_all_list_fields(self):
        """All four dedup-eligible fields are processed."""
        raw = {
            "allowed_files": ["a.py", "a.py"],
            "context_files": ["b.py", "b.py"],
            "forbidden": ["x", "x"],
            "acceptance_commands": ["cmd1", "cmd1"],
        }
        result = normalize_work_order(raw)
        assert result["allowed_files"] == ["a.py"]
        assert result["context_files"] == ["b.py"]
        assert result["forbidden"] == ["x"]
        assert result["acceptance_commands"] == ["cmd1"]

    def test_strips_nested_condition_strings(self):
        """Whitespace in nested dicts (e.g. conditions) is stripped."""
        raw = {
            "preconditions": [{"kind": "  file_exists ", "path": "  src/a.py  "}],
        }
        result = normalize_work_order(raw)
        assert result["preconditions"][0]["kind"] == "file_exists"
        assert result["preconditions"][0]["path"] == "src/a.py"

    def test_preserves_non_list_fields(self):
        """Fields that are not in the dedup list are untouched."""
        raw = {"id": "WO-01", "notes": "  keep spaces  "}
        result = normalize_work_order(raw)
        assert result["notes"] == "keep spaces"  # stripped but not deduped
