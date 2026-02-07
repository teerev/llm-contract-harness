"""Planner-level validation rules (on top of WorkOrder schema validation)."""

from __future__ import annotations

import re
from typing import Any

from factory.schemas import WorkOrder

VERIFY_COMMAND = "./scripts/verify.sh"
WO_ID_PATTERN = re.compile(r"^WO-\d{2}$")
GLOB_CHARS = set("*?[")


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
        acceptance = wo.get("acceptance_commands", [])
        if VERIFY_COMMAND not in acceptance:
            errors.append(
                f"{wo_id}: acceptance_commands must contain '{VERIFY_COMMAND}'"
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
