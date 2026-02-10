"""Main compile orchestration: prompt → LLM → validate → revise → write."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from planner.io import (
    check_overwrite,
    write_json_artifact,
    write_text_artifact,
    write_work_orders,
)
import planner.openai_client as _oai
from planner import defaults as _pd
from planner.defaults import (  # noqa: F401 — re-exported for backward compat
    COMPILE_HASH_HEX_LENGTH,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    MAX_COMPILE_ATTEMPTS,
    MAX_JSON_PAYLOAD_BYTES,
    SKIP_DIRS as _SKIP_DIRS,
)
from planner.openai_client import OpenAIResponsesClient
from planner.prompt_template import load_template, render_prompt, resolve_template_path
from planner.validation import (
    ValidationError,
    compute_verify_exempt,
    parse_and_validate,
    validate_plan_v2,
)


# ---------------------------------------------------------------------------
# Compile hash
# ---------------------------------------------------------------------------

def _compute_compile_hash(
    spec_bytes: bytes,
    template_bytes: bytes,
    model: str,
    reasoning_effort: str,
) -> str:
    """SHA-256 over spec + template + model + reasoning effort, first 16 hex chars."""
    h = hashlib.sha256()
    h.update(spec_bytes)
    h.update(b"\n")
    h.update(template_bytes)
    h.update(b"\n")
    h.update(model.encode("utf-8"))
    h.update(b"\n")
    h.update(reasoning_effort.encode("utf-8"))
    return h.hexdigest()[:COMPILE_HASH_HEX_LENGTH]


# ---------------------------------------------------------------------------
# JSON parsing from LLM output
# ---------------------------------------------------------------------------


def _parse_json(raw: str) -> dict:
    """Parse JSON from raw LLM output, stripping markdown fences if present."""
    if len(raw) > MAX_JSON_PAYLOAD_BYTES:
        raise ValueError(
            f"JSON payload too large: {len(raw)} bytes (max {MAX_JSON_PAYLOAD_BYTES})"
        )
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Repo file listing
# ---------------------------------------------------------------------------

def _build_repo_file_listing(repo_path: str) -> set[str]:
    """Walk *repo_path* and return a set of relative POSIX-style file paths."""
    result: set[str] = set()
    for root, dirs, files in os.walk(repo_path):
        # Prune directories we never want to traverse
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            abs_path = os.path.join(root, f)
            rel = os.path.relpath(abs_path, repo_path)
            # Normalize to forward slashes (POSIX) for cross-platform consistency
            result.add(rel.replace(os.sep, "/"))
    return result


# ---------------------------------------------------------------------------
# Revision prompt for compile retry loop
# ---------------------------------------------------------------------------

def _build_revision_prompt(
    spec_text: str,
    previous_response: str,
    errors: list[ValidationError],
) -> str:
    """Build a prompt that asks the planner LLM to fix validation errors.

    Includes the structured error list, the previous response, and the
    original spec for context.
    """
    error_lines = []
    for e in errors:
        error_lines.append(f"  - {e}")

    return "\n".join([
        "You previously generated a JSON manifest of work orders, but it "
        "failed validation with the errors listed below.",
        "",
        "Please fix ONLY the cited errors and output the corrected JSON "
        "manifest. Preserve all work orders and fields that are not cited "
        "in the errors. Output ONLY the corrected JSON object — no markdown "
        "fences, no commentary.",
        "",
        "## Validation Errors",
        "",
        *error_lines,
        "",
        "## Your Previous Response (to correct)",
        "",
        previous_response,
        "",
        "## Original Product Specification (for reference)",
        "",
        spec_text,
        "",
        "Output the corrected JSON object now.",
    ])


# ---------------------------------------------------------------------------
# Compile entry point
# ---------------------------------------------------------------------------

class CompileResult:
    """Result of a compile run."""

    def __init__(self) -> None:
        self.compile_hash: str = ""
        self.artifacts_dir: str = ""
        self.work_orders: list[dict] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.manifest: dict[str, Any] = {}
        self.outdir: str = ""
        self.success: bool = False
        self.compile_attempts: int = 0


def compile_plan(
    spec_path: str,
    outdir: str,
    template_path: str | None = None,
    artifacts_dir: str | None = None,
    overwrite: bool = False,
    repo_path: str | None = None,
) -> CompileResult:
    """Compile a product spec into validated work orders.

    Returns a CompileResult with all details. Raises only on truly
    unrecoverable errors (missing files, bad API key). Validation
    failures are captured in result.errors.
    """
    result = CompileResult()
    result.outdir = outdir
    ts_start = time.time()

    # --- Resolve paths ---
    template_path = resolve_template_path(template_path)
    if artifacts_dir is None:
        if os.path.isdir(os.path.join(".", "examples", "artifacts")):
            artifacts_dir = os.path.join(".", "examples", "artifacts")
        else:
            artifacts_dir = os.path.join(".", "artifacts")

    # --- Read inputs ---
    with open(spec_path, "rb") as fh:
        spec_bytes = fh.read()
    spec_text = spec_bytes.decode("utf-8")

    with open(template_path, "rb") as fh:
        template_bytes = fh.read()
    template_text = template_bytes.decode("utf-8")

    # --- Compile hash ---
    compile_hash = _compute_compile_hash(
        spec_bytes, template_bytes, DEFAULT_MODEL, DEFAULT_REASONING_EFFORT
    )
    result.compile_hash = compile_hash

    # --- Artifact directory ---
    compile_artifacts = os.path.join(artifacts_dir, compile_hash, "compile")
    os.makedirs(compile_artifacts, exist_ok=True)
    result.artifacts_dir = compile_artifacts

    # --- Repo file listing (for chain validation) ---
    repo_file_listing: set[str] = set()
    if repo_path:
        repo_file_listing = _build_repo_file_listing(repo_path)

    # --- Render initial prompt ---
    prompt = render_prompt(template_text, spec_text)
    write_text_artifact(
        os.path.join(compile_artifacts, "prompt_rendered.txt"), prompt
    )

    # --- Compile loop: generate → validate → revise ───────────────────
    _oai.DUMP_DIR = compile_artifacts
    client = OpenAIResponsesClient()

    # Track per-attempt results for the summary
    attempt_records: list[dict] = []
    final_work_orders: list[dict] = []
    final_parsed: dict = {}
    final_hard_errors: list[ValidationError] = []
    final_warnings: list[ValidationError] = []

    for attempt in range(1, MAX_COMPILE_ATTEMPTS + 1):
        # ── LLM call ─────────────────────────────────────────────────
        raw_response = client.generate_text(prompt)
        write_text_artifact(
            os.path.join(compile_artifacts, f"llm_raw_response_attempt_{attempt}.txt"),
            raw_response,
        )

        # ── Parse JSON ───────────────────────────────────────────────
        try:
            parsed = _parse_json(raw_response)
        except (json.JSONDecodeError, ValueError) as exc:
            parse_errors = [ValidationError(
                code="E000",
                wo_id=None,
                message=f"JSON parse error: {exc}",
            )]
            attempt_records.append({
                "attempt": attempt,
                "errors": [e.to_dict() for e in parse_errors],
            })
            write_json_artifact(
                os.path.join(compile_artifacts, f"validation_errors_attempt_{attempt}.json"),
                [e.to_dict() for e in parse_errors],
            )
            if attempt < MAX_COMPILE_ATTEMPTS:
                prompt = _build_revision_prompt(spec_text, raw_response, parse_errors)
                continue
            # Final attempt — still can't parse
            result.errors = [str(e) for e in parse_errors]
            result.compile_attempts = attempt
            _write_summary(result, compile_artifacts, ts_start, spec_path,
                           template_path, attempt_records)
            return result

        write_json_artifact(
            os.path.join(compile_artifacts, f"manifest_raw_attempt_{attempt}.json"),
            parsed,
        )

        # ── Structural validation (E0xx) ─────────────────────────────
        work_orders, structural_errors = parse_and_validate(parsed)

        # ── Chain validation (E1xx / W1xx) ───────────────────────────
        chain_errors = validate_plan_v2(
            work_orders,
            verify_contract=parsed.get("verify_contract"),
            repo_file_listing=repo_file_listing,
        )

        # Separate hard errors from warnings
        hard_errors = list(structural_errors) + [
            e for e in chain_errors if not e.code.startswith("W")
        ]
        warnings = [e for e in chain_errors if e.code.startswith("W")]

        all_this_attempt = hard_errors + warnings
        attempt_records.append({
            "attempt": attempt,
            "errors": [e.to_dict() for e in all_this_attempt],
        })
        write_json_artifact(
            os.path.join(compile_artifacts, f"validation_errors_attempt_{attempt}.json"),
            [e.to_dict() for e in all_this_attempt],
        )

        final_work_orders = work_orders
        final_parsed = parsed
        final_hard_errors = hard_errors
        final_warnings = warnings

        if not hard_errors:
            # Success — may have warnings but no blocking errors
            break

        if attempt < MAX_COMPILE_ATTEMPTS:
            prompt = _build_revision_prompt(spec_text, raw_response, hard_errors)
            continue

    # ── Post-loop ─────────────────────────────────────────────────────
    result.compile_attempts = len(attempt_records)
    result.warnings = [str(w) for w in final_warnings]

    if final_hard_errors:
        result.errors = [str(e) for e in final_hard_errors]
        result.work_orders = final_work_orders
        structured = [e.to_dict() for e in final_hard_errors + final_warnings]
        write_json_artifact(
            os.path.join(compile_artifacts, "validation_errors.json"), structured
        )
        os.makedirs(outdir, exist_ok=True)
        write_json_artifact(
            os.path.join(outdir, "validation_errors.json"), structured
        )
        _write_summary(result, compile_artifacts, ts_start, spec_path,
                       template_path, attempt_records)
        return result

    # ── Compute verify_exempt ─────────────────────────────────────────
    # CRITICAL (M-01): Never trust LLM-provided verify_exempt.
    # Always overwrite: compute from verify_contract if valid, else force False.
    verify_contract = final_parsed.get("verify_contract")
    if (
        isinstance(verify_contract, dict)
        and verify_contract.get("requires")
        and final_work_orders
    ):
        final_work_orders = compute_verify_exempt(
            final_work_orders, verify_contract, repo_file_listing,
        )
    else:
        # No valid verify_contract → nothing is exempt.
        final_work_orders = [
            {**wo, "verify_exempt": False} for wo in final_work_orders
        ]

    result.work_orders = final_work_orders

    # ── Build manifest and write outputs ──────────────────────────────
    manifest: dict[str, Any] = {
        "system_overview": final_parsed.get("system_overview", []),
        "verify_contract": verify_contract,
        "work_orders": final_work_orders,
    }
    result.manifest = manifest

    write_json_artifact(
        os.path.join(compile_artifacts, "manifest_normalized.json"), manifest
    )

    check_overwrite(outdir, overwrite)
    write_work_orders(outdir, final_work_orders, manifest)
    result.success = True

    _write_summary(result, compile_artifacts, ts_start, spec_path,
                   template_path, attempt_records)
    return result


def _write_summary(
    result: CompileResult,
    compile_artifacts: str,
    ts_start: float,
    spec_path: str,
    template_path: str,
    attempt_records: list[dict] | None = None,
) -> None:
    """Write compile_summary.json to the artifacts directory."""
    summary = {
        "compile_hash": result.compile_hash,
        "spec_path": os.path.abspath(spec_path),
        "template_path": os.path.abspath(template_path),
        "model": DEFAULT_MODEL,
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        "work_order_count": len(result.work_orders),
        "validation_errors": result.errors,
        "validation_warnings": result.warnings,
        "compile_attempts": result.compile_attempts,
        "attempt_records": attempt_records or [],
        "success": result.success,
        "outdir": os.path.abspath(result.outdir),
        "artifacts_dir": os.path.abspath(result.artifacts_dir),
        "start_timestamp": ts_start,
        "end_timestamp": time.time(),
        "duration_seconds": round(time.time() - ts_start, 3),
        "defaults_snapshot": {
            "default_model": _pd.DEFAULT_MODEL,
            "default_reasoning_effort": _pd.DEFAULT_REASONING_EFFORT,
            "default_max_output_tokens": _pd.DEFAULT_MAX_OUTPUT_TOKENS,
            "max_incomplete_token_cap": _pd.MAX_INCOMPLETE_TOKEN_CAP,
            "connect_timeout": _pd.CONNECT_TIMEOUT,
            "read_timeout": _pd.READ_TIMEOUT,
            "write_timeout": _pd.WRITE_TIMEOUT,
            "pool_timeout": _pd.POOL_TIMEOUT,
            "max_transport_retries": _pd.MAX_TRANSPORT_RETRIES,
            "transport_retry_base_s": _pd.TRANSPORT_RETRY_BASE_S,
            "max_incomplete_retries": _pd.MAX_INCOMPLETE_RETRIES,
            "poll_interval_s": _pd.POLL_INTERVAL_S,
            "poll_deadline_s": _pd.POLL_DEADLINE_S,
            "max_compile_attempts": _pd.MAX_COMPILE_ATTEMPTS,
            "compile_hash_hex_length": _pd.COMPILE_HASH_HEX_LENGTH,
            "max_json_payload_bytes": _pd.MAX_JSON_PAYLOAD_BYTES,
        },
    }
    write_json_artifact(
        os.path.join(compile_artifacts, "compile_summary.json"), summary
    )
