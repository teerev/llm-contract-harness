"""Main compile orchestration: prompt → LLM → validate → revise → write."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

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
from planner.openai_client import LLMResult, OpenAIResponsesClient
from planner.prompt_template import load_template, render_prompt, resolve_template_path
from planner.validation import (
    ValidationError,
    compute_verify_exempt,
    parse_and_validate,
    validate_plan_v2,
)
from shared.run_context import (
    generate_ulid,
    get_tool_version,
    resolve_artifacts_root,
    sha256_bytes,
    sha256_json,
    utc_now_iso,
    write_run_json,
)


# ---------------------------------------------------------------------------
# Compile lifecycle callback (for CLI output)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AttemptEvent:
    """Structured event emitted during each compile attempt.

    The CLI subscribes to these events to print per-attempt progress,
    verdicts, and error summaries to the terminal.
    """
    kind: str          # "start", "fail", "pass"
    attempt: int       # 1-based attempt number
    max_attempts: int  # total allowed attempts
    # Only populated on "fail":
    errors: list[ValidationError] | None = None
    errors_artifact: str | None = None
    is_final: bool = False  # True when this is the last attempt (no retry)


# Type alias for the callback
AttemptCallback = Optional[Callable[[AttemptEvent], None]]


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
# Bootstrap WO filtering
# ---------------------------------------------------------------------------


def _renumber_work_orders(work_orders: list[dict]) -> list[dict]:
    """Renumber work order IDs to be contiguous from WO-01."""
    result = []
    for i, wo in enumerate(work_orders, start=1):
        new_id = f"WO-{i:02d}"
        result.append({**wo, "id": new_id})
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
        self.run_id: str = ""
        self.run_dir: str = ""
        self.compile_hash: str = ""
        self.artifacts_dir: str = ""
        self.work_orders: list[dict] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.manifest: dict[str, Any] = {}
        self.outdir: str = ""
        self.success: bool = False
        self.compile_attempts: int = 0
        self.bootstrap_skipped: bool = False
        self.bootstrap_reason: str | None = None


def compile_plan(
    spec_path: str,
    outdir: str | None = None,
    template_path: str | None = None,
    artifacts_dir: str | None = None,
    overwrite: bool = False,
    repo_path: str | None = None,
    on_attempt: AttemptCallback = None,
) -> CompileResult:
    """Compile a product spec into validated work orders.

    Canonical artifacts are always written under ``artifacts_dir/planner/{run_id}/``.
    If *outdir* is provided, work order files are also exported there.

    Returns a CompileResult with all details. Raises only on truly
    unrecoverable errors (missing files, bad API key). Validation
    failures are captured in result.errors.
    """
    result = CompileResult()
    result.outdir = outdir or ""
    started_at = utc_now_iso()
    ts_start = time.time()

    # --- Resolve paths ---
    template_path = resolve_template_path(template_path)
    artifacts_root = resolve_artifacts_root(artifacts_dir)

    # --- Generate unique run_id and create immutable run directory ---
    run_id = generate_ulid()
    run_dir = os.path.join(artifacts_root, "planner", run_id)
    os.makedirs(run_dir, exist_ok=False)

    compile_artifacts = os.path.join(run_dir, "compile")
    os.makedirs(compile_artifacts)
    canonical_output = os.path.join(run_dir, "output")

    result.run_id = run_id
    result.run_dir = run_dir
    result.artifacts_dir = compile_artifacts

    # --- Read inputs ---
    with open(spec_path, "rb") as fh:
        spec_bytes = fh.read()
    spec_text = spec_bytes.decode("utf-8")

    with open(template_path, "rb") as fh:
        template_bytes = fh.read()
    template_text = template_bytes.decode("utf-8")

    # --- Compile hash (content-addressable, NOT used as directory name) ---
    compile_hash = _compute_compile_hash(
        spec_bytes, template_bytes, DEFAULT_MODEL, DEFAULT_REASONING_EFFORT
    )
    result.compile_hash = compile_hash

    # --- Write run.json early (incomplete — updated on finish) ---
    run_json: dict[str, Any] = {
        "run_id": run_id,
        "tool": "planner",
        "started_at_utc": started_at,
        "finished_at_utc": None,
        "success": None,
        "compile_hash": compile_hash,
        "version": get_tool_version(),
        "config": {
            "model": DEFAULT_MODEL,
            "reasoning_effort": DEFAULT_REASONING_EFFORT,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "max_compile_attempts": MAX_COMPILE_ATTEMPTS,
        },
        "inputs": {
            "spec_path": os.path.abspath(spec_path),
            "spec_sha256": sha256_bytes(spec_bytes),
            "template_path": os.path.abspath(template_path),
            "template_sha256": sha256_bytes(template_bytes),
        },
        "outputs": None,
        "artifacts": {
            "compile_dir": "compile/",
            "output_dir": "output/",
        },
        "export": {
            "outdir": os.path.abspath(outdir) if outdir else None,
        },
    }
    write_run_json(run_dir, run_json)

    # --- Repo file listing (for chain validation) ---
    repo_file_listing: set[str] = set()
    if repo_path:
        repo_file_listing = _build_repo_file_listing(repo_path)

    # --- Render initial prompt ---
    prompt = render_prompt(template_text, spec_text)

    # --- Compile loop: generate → validate → revise ───────────────────
    _oai.DUMP_DIR = compile_artifacts
    client = OpenAIResponsesClient()

    # Track per-attempt results for the summary
    attempt_records: list[dict] = []
    final_work_orders: list[dict] = []
    final_parsed: dict = {}
    final_hard_errors: list[ValidationError] = []
    final_warnings: list[ValidationError] = []

    def _emit(kind: str, attempt: int, **kwargs: Any) -> None:
        if on_attempt is not None:
            on_attempt(AttemptEvent(
                kind=kind,
                attempt=attempt,
                max_attempts=MAX_COMPILE_ATTEMPTS,
                **kwargs,
            ))

    for attempt in range(1, MAX_COMPILE_ATTEMPTS + 1):
        _emit("start", attempt)

        # ── Save prompt for this attempt ──────────────────────────────
        write_text_artifact(
            os.path.join(compile_artifacts, f"prompt_attempt_{attempt}.txt"),
            prompt,
        )

        # ── LLM call ─────────────────────────────────────────────────
        llm_result: LLMResult = client.generate_text(prompt)
        raw_response = llm_result.text
        write_text_artifact(
            os.path.join(compile_artifacts, f"llm_raw_response_attempt_{attempt}.txt"),
            raw_response,
        )
        if llm_result.reasoning:
            write_text_artifact(
                os.path.join(compile_artifacts, f"llm_reasoning_attempt_{attempt}.txt"),
                llm_result.reasoning,
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
            errors_path = os.path.join(
                compile_artifacts, f"validation_errors_attempt_{attempt}.json"
            )
            write_json_artifact(errors_path, [e.to_dict() for e in parse_errors])
            is_final = attempt >= MAX_COMPILE_ATTEMPTS
            _emit("fail", attempt, errors=parse_errors,
                  errors_artifact=errors_path, is_final=is_final)
            if not is_final:
                prompt = _build_revision_prompt(spec_text, raw_response, parse_errors)
                continue
            # Final attempt — still can't parse
            result.errors = [str(e) for e in parse_errors]
            result.compile_attempts = attempt
            _write_summary(result, compile_artifacts, ts_start, spec_path,
                           template_path, attempt_records)
            _finalize_run_json(run_dir, run_json, result, attempt_records)
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
        errors_path = os.path.join(
            compile_artifacts, f"validation_errors_attempt_{attempt}.json"
        )
        write_json_artifact(errors_path, [e.to_dict() for e in all_this_attempt])

        final_work_orders = work_orders
        final_parsed = parsed
        final_hard_errors = hard_errors
        final_warnings = warnings

        if not hard_errors:
            # Success — may have warnings but no blocking errors
            _emit("pass", attempt)
            break

        is_final = attempt >= MAX_COMPILE_ATTEMPTS
        _emit("fail", attempt, errors=hard_errors,
              errors_artifact=errors_path, is_final=is_final)

        if not is_final:
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
        if outdir:
            os.makedirs(outdir, exist_ok=True)
            write_json_artifact(
                os.path.join(outdir, "validation_errors.json"), structured
            )
        _write_summary(result, compile_artifacts, ts_start, spec_path,
                       template_path, attempt_records)
        _finalize_run_json(run_dir, run_json, result, attempt_records)
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

    # ── Skip bootstrap WO if verify script already exists in repo ──────
    # Detection: a WO is "bootstrap" if its postconditions create
    # scripts/verify.sh. If the repo already has it, the bootstrap WO's
    # precondition (file_absent) would fail at factory runtime. Filtering
    # it here is a planner-level adaptation, not a weakening of enforcement.
    VERIFY_SCRIPT = "scripts/verify.sh"
    bootstrap_skipped = False
    bootstrap_reason = None

    if repo_file_listing and VERIFY_SCRIPT in repo_file_listing:
        filtered: list[dict] = []
        for wo in final_work_orders:
            postcond_paths = {
                c.get("path", "") for c in wo.get("postconditions", [])
                if isinstance(c, dict)
            }
            if VERIFY_SCRIPT in postcond_paths:
                bootstrap_skipped = True
                bootstrap_reason = (
                    f"{VERIFY_SCRIPT} already present in repo baseline — "
                    f"skipped {wo.get('id', '?')} \"{wo.get('title', '')}\""
                )
                continue
            filtered.append(wo)

        if bootstrap_skipped and filtered:
            # Renumber WO IDs to stay contiguous from WO-01
            final_work_orders = _renumber_work_orders(filtered)

    result.work_orders = final_work_orders
    result.bootstrap_skipped = bootstrap_skipped
    result.bootstrap_reason = bootstrap_reason

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

    # ── Inject provenance into work orders ────────────────────────────
    manifest_sha256 = sha256_json(manifest)
    base_provenance = {
        "planner_run_id": run_id,
        "compile_hash": compile_hash,
        "manifest_sha256": manifest_sha256,
    }
    final_work_orders = [
        {
            **wo,
            "provenance": {
                **base_provenance,
                # Mark bootstrap WOs: verify_exempt=True means the planner
                # determined this WO runs before the verify infrastructure is
                # complete. The factory can auto-honor verify_exempt for these.
                "bootstrap": bool(wo.get("verify_exempt")),
            },
        }
        for wo in final_work_orders
    ]
    result.work_orders = final_work_orders

    # Update manifest with provenance-bearing WOs
    manifest["work_orders"] = final_work_orders

    # ── Write canonical output ────────────────────────────────────────
    write_work_orders(canonical_output, final_work_orders, manifest)

    # ── Optional export to user-specified outdir ──────────────────────
    if outdir:
        check_overwrite(outdir, overwrite)
        write_work_orders(outdir, final_work_orders, manifest)

    result.success = True

    _write_summary(result, compile_artifacts, ts_start, spec_path,
                   template_path, attempt_records)
    _finalize_run_json(run_dir, run_json, result, attempt_records)
    return result


def _finalize_run_json(
    run_dir: str,
    run_json: dict,
    result: CompileResult,
    attempt_records: list[dict],
) -> None:
    """Update run.json with final outputs and timestamps."""
    run_json["finished_at_utc"] = utc_now_iso()
    run_json["success"] = result.success
    run_json["outputs"] = {
        "work_order_count": len(result.work_orders),
        "manifest_normalized_sha256": sha256_json(result.manifest) if result.manifest else None,
        "compile_attempts": result.compile_attempts,
        "bootstrap_skipped": result.bootstrap_skipped,
        "bootstrap_reason": result.bootstrap_reason,
        "validation_errors": result.errors,
        "validation_warnings": result.warnings,
        "attempt_records": attempt_records,
    }
    write_run_json(run_dir, run_json)


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
        "bootstrap_skipped": result.bootstrap_skipped,
        "bootstrap_reason": result.bootstrap_reason,
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
