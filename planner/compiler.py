"""Main compile orchestration: prompt → LLM → validate → write."""

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
from planner.openai_client import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    OpenAIResponsesClient,
)
from planner.prompt_template import load_template, render_prompt, resolve_template_path
from planner.validation import parse_and_validate


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
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# JSON parsing from LLM output
# ---------------------------------------------------------------------------

def _parse_json(raw: str) -> dict:
    """Parse JSON from raw LLM output, stripping markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


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
        self.manifest: dict[str, Any] = {}
        self.outdir: str = ""
        self.success: bool = False


def compile_plan(
    spec_path: str,
    outdir: str,
    template_path: str | None = None,
    artifacts_dir: str | None = None,
    overwrite: bool = False,
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

    # --- Render prompt ---
    prompt = render_prompt(template_text, spec_text)
    write_text_artifact(
        os.path.join(compile_artifacts, "prompt_rendered.txt"), prompt
    )

    # --- Call LLM ---
    _oai.DUMP_DIR = compile_artifacts  # dump raw responses here on failure
    client = OpenAIResponsesClient()
    raw_response = client.generate_text(prompt)
    write_text_artifact(
        os.path.join(compile_artifacts, "llm_raw_response.txt"), raw_response
    )

    # --- Parse JSON ---
    try:
        parsed = _parse_json(raw_response)
    except (json.JSONDecodeError, ValueError) as exc:
        result.errors = [f"JSON parse error: {exc}"]
        write_json_artifact(
            os.path.join(compile_artifacts, "validation_errors.json"),
            result.errors,
        )
        _write_summary(result, compile_artifacts, ts_start, spec_path, template_path)
        return result

    write_json_artifact(
        os.path.join(compile_artifacts, "manifest_raw.json"), parsed
    )

    # --- Validate ---
    work_orders, validation_errors = parse_and_validate(parsed)
    result.work_orders = work_orders
    result.errors = [str(e) for e in validation_errors]

    # Build normalized manifest
    manifest: dict[str, Any] = {
        "system_overview": parsed.get("system_overview", []),
        "work_orders": work_orders,
    }
    result.manifest = manifest

    write_json_artifact(
        os.path.join(compile_artifacts, "manifest_normalized.json"), manifest
    )

    if validation_errors:
        # Write structured errors (list of dicts) for machine consumption,
        # plus the string list via result.errors for CLI / summary.
        structured = [e.to_dict() for e in validation_errors]
        write_json_artifact(
            os.path.join(compile_artifacts, "validation_errors.json"), structured
        )
        # Also write to outdir for visibility
        os.makedirs(outdir, exist_ok=True)
        write_json_artifact(
            os.path.join(outdir, "validation_errors.json"), structured
        )
        _write_summary(result, compile_artifacts, ts_start, spec_path, template_path)
        return result

    # --- Check overwrite + write outputs ---
    check_overwrite(outdir, overwrite)
    write_work_orders(outdir, work_orders, manifest)
    result.success = True

    _write_summary(result, compile_artifacts, ts_start, spec_path, template_path)
    return result


def _write_summary(
    result: CompileResult,
    compile_artifacts: str,
    ts_start: float,
    spec_path: str,
    template_path: str,
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
        "success": result.success,
        "outdir": os.path.abspath(result.outdir),
        "artifacts_dir": os.path.abspath(result.artifacts_dir),
        "start_timestamp": ts_start,
        "end_timestamp": time.time(),
        "duration_seconds": round(time.time() - ts_start, 3),
    }
    write_json_artifact(
        os.path.join(compile_artifacts, "compile_summary.json"), summary
    )
