"""Planner-level validation rules (on top of WorkOrder schema validation)."""

from __future__ import annotations

import re
import shlex
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


def validate_plan(work_orders_raw: list[dict]) -> list[str]:
    """Validate a list of raw work-order dicts. Return a list of error strings.

    An empty list means all validations passed.
    """
    errors: list[str] = []

    if not work_orders_raw:
        errors.append("work_orders list is empty")
        return errors

    # --- Normalize all work orders first ---
    normalized: list[dict] = [normalize_work_order(wo) for wo in work_orders_raw]

    # --- 1) ID format and contiguity ---
    for i, wo in enumerate(normalized):
        wo_id = wo.get("id", "")
        expected_id = f"WO-{i + 1:02d}"
        if not WO_ID_PATTERN.match(wo_id):
            errors.append(
                f"Work order {i}: id {wo_id!r} does not match pattern WO-NN"
            )
        elif wo_id != expected_id:
            errors.append(
                f"Work order {i}: id {wo_id!r} should be {expected_id!r} "
                f"(must be contiguous from WO-01)"
            )

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
            errors.append(
                f"{wo_id}: acceptance_commands must contain '{VERIFY_COMMAND}'"
            )

        # Shell operators — commands run with shell=False, so pipes,
        # redirects, and chaining operators will not work.  We check the
        # shlex-split tokens (not the raw string) so that characters inside
        # quoted arguments (e.g. python -c "a; b") are not flagged.
        for cmd_str in acceptance:
            try:
                tokens = shlex.split(cmd_str)
            except ValueError:
                continue  # shlex parse errors are caught elsewhere
            bad = [t for t in tokens if t in SHELL_OPERATOR_TOKENS]
            if bad:
                errors.append(
                    f"{wo_id}: acceptance command contains shell operator "
                    f"token(s) {bad} which are incompatible with shell=False "
                    f"execution: {cmd_str!r}"
                )

        # Path hygiene — reject glob characters
        for field in ("allowed_files", "context_files"):
            for path in wo.get(field, []):
                if any(c in path for c in GLOB_CHARS):
                    errors.append(
                        f"{wo_id}: {field} contains glob character in '{path}'"
                    )

        # Validate against WorkOrder schema
        try:
            WorkOrder(**wo)
        except Exception as exc:
            errors.append(f"{wo_id}: schema validation failed: {exc}")

    return errors


def parse_and_validate(raw_json: dict) -> tuple[list[dict], list[str]]:
    """Parse the top-level LLM response, normalize, and validate.

    Returns (normalized_work_orders, errors).
    """
    if not isinstance(raw_json, dict):
        return [], ["Top-level JSON must be an object"]

    work_orders_raw = raw_json.get("work_orders")
    if not isinstance(work_orders_raw, list):
        return [], ["Missing or invalid 'work_orders' key in response"]

    # Normalize
    normalized = [normalize_work_order(wo) for wo in work_orders_raw]

    # Validate
    errors = validate_plan(work_orders_raw)

    return normalized, errors
