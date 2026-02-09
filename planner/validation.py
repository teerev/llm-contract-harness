"""Planner-level validation rules (on top of WorkOrder schema validation)."""

from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass, field as dc_field
from typing import Any

from factory.schemas import WorkOrder

VERIFY_COMMAND = "bash scripts/verify.sh"
VERIFY_SCRIPT_PATH = "scripts/verify.sh"
WO_ID_PATTERN = re.compile(r"^WO-\d{2}$")
GLOB_CHARS = set("*?[")
# Bare tokens that indicate shell operators — incompatible with shell=False.
# Checked against individual tokens after shlex.split, so semicolons and
# other characters *inside* quoted strings (e.g. python -c "a; b") are safe.
SHELL_OPERATOR_TOKENS = frozenset({"|", "||", "&&", ";", ">", ">>", "<", "<<"})


# ---------------------------------------------------------------------------
# Structured validation errors
# ---------------------------------------------------------------------------

# Error code constants — machine-readable, stable across versions.
E000_STRUCTURAL = "E000"  # Top-level structure errors (empty list, bad JSON shape)
E001_ID = "E001"          # ID format / contiguity
E002_VERIFY = "E002"      # Verify command missing in acceptance
E003_SHELL_OP = "E003"    # Shell operator in acceptance command
E004_GLOB = "E004"        # Glob character in path
E005_SCHEMA = "E005"      # Pydantic schema validation failed
E006_SYNTAX = "E006"      # Python syntax error in python -c command


@dataclass(frozen=True)
class ValidationError:
    """A structured validation error with a machine-readable code.

    Designed so that error codes can be fed back to the planner LLM in a
    revision prompt (M5 compile retry loop) for precise self-correction.
    """

    code: str
    wo_id: str | None
    message: str
    field: str | None = None

    def __str__(self) -> str:
        parts = [f"[{self.code}]"]
        if self.wo_id:
            parts.append(f"{self.wo_id}:")
        parts.append(self.message)
        return " ".join(parts)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON artifact output."""
        return {
            "code": self.code,
            "wo_id": self.wo_id,
            "message": self.message,
            "field": self.field,
        }


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _strip_strings(obj: Any) -> Any:
    """Recursively strip whitespace from all string values in a dict/list."""
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, list):
        return [_strip_strings(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _strip_strings(v) for k, v in obj.items()}
    return obj


def _deduplicate(items: list) -> list:
    """Deduplicate list preserving first-occurrence order."""
    seen: set = set()
    result: list = []
    for item in items:
        key = item if isinstance(item, str) else id(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def normalize_work_order(raw: dict) -> dict:
    """Strip whitespace and deduplicate list fields. Returns a new dict."""
    cleaned = _strip_strings(raw)
    for list_key in ("allowed_files", "context_files", "forbidden", "acceptance_commands"):
        if list_key in cleaned and isinstance(cleaned[list_key], list):
            cleaned[list_key] = _deduplicate(cleaned[list_key])
    return cleaned


# ---------------------------------------------------------------------------
# Python -c syntax checking
# ---------------------------------------------------------------------------


def _check_python_c_syntax(
    cmd_str: str, wo_id: str
) -> ValidationError | None:
    """If *cmd_str* is a ``python -c "..."`` command, syntax-check the code.

    Returns a ``ValidationError`` with code ``E006`` on ``SyntaxError``,
    or ``None`` if the command is not a ``python -c`` invocation or the
    syntax is valid.
    """
    try:
        tokens = shlex.split(cmd_str)
    except ValueError:
        return None  # shlex parse errors are surfaced by the shell-op check

    if len(tokens) >= 3 and tokens[0] == "python" and tokens[1] == "-c":
        code = tokens[2]
        try:
            ast.parse(code)
        except SyntaxError as exc:
            line_info = f" (line {exc.lineno})" if exc.lineno else ""
            return ValidationError(
                code=E006_SYNTAX,
                wo_id=wo_id,
                message=(
                    f"Python syntax error in acceptance command{line_info}: "
                    f"{exc.msg}: {cmd_str!r}"
                ),
                field="acceptance_commands",
            )
    return None


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------


def validate_plan(work_orders_raw: list[dict]) -> list[ValidationError]:
    """Validate a list of raw work-order dicts.

    Returns a list of ``ValidationError`` objects. An empty list means all
    validations passed.
    """
    errors: list[ValidationError] = []

    if not work_orders_raw:
        errors.append(ValidationError(
            code=E000_STRUCTURAL,
            wo_id=None,
            message="work_orders list is empty",
        ))
        return errors

    # --- Normalize all work orders first ---
    normalized: list[dict] = [normalize_work_order(wo) for wo in work_orders_raw]

    # --- 1) ID format and contiguity ---
    for i, wo in enumerate(normalized):
        wo_id = wo.get("id", "")
        expected_id = f"WO-{i + 1:02d}"
        if not WO_ID_PATTERN.match(wo_id):
            errors.append(ValidationError(
                code=E001_ID,
                wo_id=wo_id or f"index-{i}",
                message=f"id {wo_id!r} does not match pattern WO-NN",
                field="id",
            ))
        elif wo_id != expected_id:
            errors.append(ValidationError(
                code=E001_ID,
                wo_id=wo_id,
                message=(
                    f"id {wo_id!r} should be {expected_id!r} "
                    f"(must be contiguous from WO-01)"
                ),
                field="id",
            ))

    # --- 2) Per-work-order checks ---
    for i, wo in enumerate(normalized):
        wo_id = wo.get("id", f"index-{i}")

        # Global verification command
        # WO-01 is exempt when it creates verify.sh (bootstrapping constraint:
        # it cannot use bash scripts/verify.sh as acceptance for the file it
        # is itself creating). All other work orders must include it.
        acceptance = wo.get("acceptance_commands", [])
        allowed = wo.get("allowed_files", [])
        is_bootstrap = wo.get("id") == "WO-01" and VERIFY_SCRIPT_PATH in allowed
        if not is_bootstrap and VERIFY_COMMAND not in acceptance:
            errors.append(ValidationError(
                code=E002_VERIFY,
                wo_id=wo_id,
                message=f"acceptance_commands must contain '{VERIFY_COMMAND}'",
                field="acceptance_commands",
            ))

        # Shell operators — commands run with shell=False, so pipes,
        # redirects, and chaining operators will not work.  We check the
        # shlex-split tokens (not the raw string) so that characters inside
        # quoted arguments (e.g. python -c "a; b") are safe.
        for cmd_str in acceptance:
            try:
                tokens = shlex.split(cmd_str)
            except ValueError:
                continue  # shlex parse errors are caught elsewhere
            bad = [t for t in tokens if t in SHELL_OPERATOR_TOKENS]
            if bad:
                errors.append(ValidationError(
                    code=E003_SHELL_OP,
                    wo_id=wo_id,
                    message=(
                        f"acceptance command contains shell operator "
                        f"token(s) {bad} which are incompatible with shell=False "
                        f"execution: {cmd_str!r}"
                    ),
                    field="acceptance_commands",
                ))

        # Python -c syntax check
        for cmd_str in acceptance:
            err = _check_python_c_syntax(cmd_str, wo_id)
            if err is not None:
                errors.append(err)

        # Path hygiene — reject glob characters
        for field_name in ("allowed_files", "context_files"):
            for path in wo.get(field_name, []):
                if any(c in path for c in GLOB_CHARS):
                    errors.append(ValidationError(
                        code=E004_GLOB,
                        wo_id=wo_id,
                        message=f"{field_name} contains glob character in '{path}'",
                        field=field_name,
                    ))

        # Validate against WorkOrder schema
        try:
            WorkOrder(**wo)
        except Exception as exc:
            errors.append(ValidationError(
                code=E005_SCHEMA,
                wo_id=wo_id,
                message=f"schema validation failed: {exc}",
            ))

    return errors


def parse_and_validate(
    raw_json: dict,
) -> tuple[list[dict], list[ValidationError]]:
    """Parse the top-level LLM response, normalize, and validate.

    Returns ``(normalized_work_orders, errors)``.
    """
    if not isinstance(raw_json, dict):
        return [], [ValidationError(
            code=E000_STRUCTURAL,
            wo_id=None,
            message="Top-level JSON must be an object",
        )]

    work_orders_raw = raw_json.get("work_orders")
    if not isinstance(work_orders_raw, list):
        return [], [ValidationError(
            code=E000_STRUCTURAL,
            wo_id=None,
            message="Missing or invalid 'work_orders' key in response",
        )]

    # Normalize
    normalized = [normalize_work_order(wo) for wo in work_orders_raw]

    # Validate
    errors = validate_plan(work_orders_raw)

    return normalized, errors
