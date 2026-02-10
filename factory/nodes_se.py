"""SE node — prompt construction, LLM call, parse response to WriteProposal."""

from __future__ import annotations

import os

from factory import llm
from factory.schemas import FailureBrief, WorkOrder, WriteProposal
from factory.util import (
    ARTIFACT_FAILURE_BRIEF,
    ARTIFACT_PROPOSED_WRITES,
    ARTIFACT_RAW_LLM_RESPONSE,
    ARTIFACT_SE_PROMPT,
    make_attempt_dir,
    save_json,
    sha256_file,
    truncate,
)

MAX_CONTEXT_BYTES = 200 * 1024  # 200 KB total for context-file reading


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_context_files(work_order: WorkOrder, repo_root: str) -> list[dict]:
    """Read context files from the repo, bounded by MAX_CONTEXT_BYTES total."""
    result: list[dict] = []
    total_bytes = 0
    for rel_path in sorted(work_order.context_files):
        abs_path = os.path.join(repo_root, rel_path)
        file_hash = sha256_file(abs_path)

        if not os.path.isfile(abs_path):
            result.append(
                {"path": rel_path, "sha256": file_hash, "content": "", "exists": False}
            )
            continue

        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        content_len = len(content.encode("utf-8"))

        if total_bytes + content_len > MAX_CONTEXT_BYTES:
            remaining = MAX_CONTEXT_BYTES - total_bytes
            if remaining > 0:
                content = content[:remaining] + "\n...[truncated to fit context budget]"
                content_len = remaining
            else:
                content = "...[context budget exhausted]"
                content_len = 0

        total_bytes += content_len
        result.append(
            {"path": rel_path, "sha256": file_hash, "content": content, "exists": True}
        )
        if total_bytes >= MAX_CONTEXT_BYTES:
            break

    return result


_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "FACTORY_PROMPT.md"
)


def _load_se_template() -> str:
    """Read the SE prompt template from disk."""
    with open(_TEMPLATE_PATH, "r", encoding="utf-8") as fh:
        return fh.read().rstrip("\n")


def _build_prompt(
    work_order: WorkOrder,
    context_files: list[dict],
    failure_brief: FailureBrief | None,
) -> str:
    """Render the SE prompt by filling dynamic values into the template.

    The static prompt structure lives in ``FACTORY_PROMPT.md`` (next to
    this module).  Dynamic sections — work-order fields, context-file
    contents, and the optional retry failure brief — are substituted at
    render time via ``str.replace`` calls, mirroring the planner's
    template approach.
    """
    template = _load_se_template()

    # ── Simple substitutions ──────────────────────────────────────────
    allowed = "\n".join(f"  - {p}" for p in sorted(work_order.allowed_files))

    # ── Conditional sections (empty string when absent) ───────────────
    forbidden = ""
    if work_order.forbidden:
        items = "\n".join(f"  - {f}" for f in work_order.forbidden)
        forbidden = f"## Forbidden\n{items}\n\n"

    notes = ""
    if work_order.notes:
        notes = f"## Notes\n{work_order.notes}\n\n"

    # ── Context files ─────────────────────────────────────────────────
    blocks: list[str] = []
    for cf in context_files:
        parts = [
            f"### {cf['path']}",
            f"exists: {cf['exists']}",
            f"sha256: {cf['sha256']}",
        ]
        if cf["content"]:
            parts.append(f"```\n{cf['content']}\n```")
        else:
            parts.append("(empty / does not exist)")
        blocks.append("\n".join(parts))
    ctx = "\n\n".join(blocks)

    # ── Failure brief (only on retry) ─────────────────────────────────
    fb = ""
    if failure_brief is not None:
        fb_lines = ["## Previous Attempt FAILED — please fix the issues"]
        fb_lines.append(f"Stage: {failure_brief.stage}")
        if failure_brief.command:
            fb_lines.append(f"Command: {failure_brief.command}")
        if failure_brief.exit_code is not None:
            fb_lines.append(f"Exit code: {failure_brief.exit_code}")
        fb_lines.append(f"Error excerpt:\n{failure_brief.primary_error_excerpt}")
        fb_lines.append(f"Reminder: {failure_brief.constraints_reminder}")
        fb = "\n".join(fb_lines) + "\n\n"

    # ── Assemble ──────────────────────────────────────────────────────
    return (
        template
        .replace("{{TITLE}}", work_order.title)
        .replace("{{INTENT}}", work_order.intent)
        .replace("{{ALLOWED_FILES}}", allowed)
        .replace("{{FORBIDDEN}}", forbidden)
        .replace("{{NOTES}}", notes)
        .replace("{{CONTEXT_FILES}}", ctx)
        .replace("{{FAILURE_BRIEF}}", fb)
    )


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


def se_node(state: dict) -> dict:
    """SE node — build prompt → call LLM → parse WriteProposal."""
    work_order = WorkOrder(**state["work_order"])
    repo_root: str = state["repo_root"]
    attempt_index: int = state["attempt_index"]
    run_id: str = state["run_id"]
    out_dir: str = state["out_dir"]

    attempt_dir = make_attempt_dir(out_dir, run_id, attempt_index)
    os.makedirs(attempt_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 0. Precondition gate — check BEFORE reading context or calling LLM.
    #    A precondition failure is a PLANNER-CONTRACT BUG, not an executor
    #    error.  The executor LLM cannot fix it, so retrying is pointless.
    # ------------------------------------------------------------------
    for cond in work_order.preconditions:
        abs_path = os.path.join(repo_root, cond.path)
        if cond.kind == "file_exists" and not os.path.isfile(abs_path):
            fb = FailureBrief(
                stage="preflight",
                primary_error_excerpt=(
                    f"PLANNER-CONTRACT BUG: precondition "
                    f"file_exists('{cond.path}') is false. "
                    f"The file does not exist."
                ),
                constraints_reminder=(
                    "This is a plan-level error. The work order sequence "
                    "is invalid. Re-run the planner."
                ),
            )
            save_json(
                fb.model_dump(),
                os.path.join(attempt_dir, ARTIFACT_FAILURE_BRIEF),
            )
            return {
                "proposal": None,
                "write_ok": False,
                "failure_brief": fb.model_dump(),
            }
        elif cond.kind == "file_absent" and os.path.isfile(abs_path):
            fb = FailureBrief(
                stage="preflight",
                primary_error_excerpt=(
                    f"PLANNER-CONTRACT BUG: precondition "
                    f"file_absent('{cond.path}') is false. "
                    f"The file already exists."
                ),
                constraints_reminder=(
                    "This is a plan-level error. The work order sequence "
                    "is invalid. Re-run the planner."
                ),
            )
            save_json(
                fb.model_dump(),
                os.path.join(attempt_dir, ARTIFACT_FAILURE_BRIEF),
            )
            return {
                "proposal": None,
                "write_ok": False,
                "failure_brief": fb.model_dump(),
            }

    # Failure brief from a prior attempt (if retrying)
    prev_fb: FailureBrief | None = None
    if state.get("failure_brief"):
        prev_fb = FailureBrief(**state["failure_brief"])

    # Read context
    context_files = _read_context_files(work_order, repo_root)

    # Build prompt
    prompt = _build_prompt(work_order, context_files, prev_fb)

    # Persist the full prompt for post-mortem auditability
    prompt_path = os.path.join(attempt_dir, ARTIFACT_SE_PROMPT)
    with open(prompt_path, "w", encoding="utf-8") as fh:
        fh.write(prompt)

    # --- Call LLM ---
    try:
        raw = llm.complete(
            prompt=prompt,
            model=state["llm_model"],
            temperature=state["llm_temperature"],
            timeout=state["timeout_seconds"],
        )
    except Exception as exc:
        fb = FailureBrief(
            stage="exception",
            primary_error_excerpt=truncate(str(exc)),
            constraints_reminder="LLM API call failed. Check OPENAI_API_KEY and model name.",
        )
        # Write-ahead: persist now in case process is killed before finalize runs.
        save_json(fb.model_dump(), os.path.join(attempt_dir, ARTIFACT_FAILURE_BRIEF))
        return {"proposal": None, "write_ok": False, "failure_brief": fb.model_dump()}

    # --- Parse response ---
    try:
        parsed = llm.parse_proposal_json(raw)
        proposal = WriteProposal(**parsed)
    except Exception as exc:
        fb = FailureBrief(
            stage="llm_output_invalid",
            primary_error_excerpt=truncate(
                f"Parse error: {exc}\nRaw response (first 500 chars): {raw[:500]}"
            ),
            constraints_reminder=(
                "LLM must output valid JSON with keys 'summary' and 'writes'. "
                "Each write needs 'path', 'base_sha256', and 'content'."
            ),
        )
        # Save raw response for debugging
        save_json(
            {"raw_response": raw},
            os.path.join(attempt_dir, ARTIFACT_RAW_LLM_RESPONSE),
        )
        # Write-ahead: persist now in case process is killed before finalize runs.
        save_json(fb.model_dump(), os.path.join(attempt_dir, ARTIFACT_FAILURE_BRIEF))
        return {"proposal": None, "write_ok": False, "failure_brief": fb.model_dump()}

    # Save proposal artifact
    save_json(
        proposal.model_dump(), os.path.join(attempt_dir, ARTIFACT_PROPOSED_WRITES)
    )

    return {"proposal": proposal.model_dump(), "write_ok": False, "failure_brief": None}
