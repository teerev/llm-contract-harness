"""SE node — prompt construction, LLM call, parse response to WriteProposal."""

from __future__ import annotations

import os

from factory import llm
from factory.schemas import FailureBrief, WorkOrder, WriteProposal
from factory.util import save_json, sha256_file, truncate

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


def _build_prompt(
    work_order: WorkOrder,
    context_files: list[dict],
    failure_brief: FailureBrief | None,
) -> str:
    """Construct the SE prompt for the LLM."""
    lines: list[str] = []

    lines.append(
        "You are a software engineer. Propose DIRECT FILE WRITES to implement "
        "the requested changes."
    )
    lines.append("")

    # --- Work-order details ---
    lines.append("## Work Order")
    lines.append(f"Title: {work_order.title}")
    lines.append(f"Intent: {work_order.intent}")
    lines.append("")

    lines.append("## Allowed Files (you may ONLY write to these paths)")
    for p in sorted(work_order.allowed_files):
        lines.append(f"  - {p}")
    lines.append("")

    if work_order.forbidden:
        lines.append("## Forbidden")
        for f in work_order.forbidden:
            lines.append(f"  - {f}")
        lines.append("")

    if work_order.notes:
        lines.append(f"## Notes\n{work_order.notes}")
        lines.append("")

    # --- Context files ---
    lines.append("## Current File Contents")
    lines.append(
        "Use the sha256 shown below as the `base_sha256` value in your writes."
    )
    lines.append(
        "For files that do not exist yet, use the sha256 of empty bytes: "
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    lines.append("")

    for cf in context_files:
        lines.append(f"### {cf['path']}")
        lines.append(f"exists: {cf['exists']}")
        lines.append(f"sha256: {cf['sha256']}")
        if cf["content"]:
            lines.append(f"```\n{cf['content']}\n```")
        else:
            lines.append("(empty / does not exist)")
        lines.append("")

    # --- Previous failure ---
    if failure_brief is not None:
        lines.append("## Previous Attempt FAILED — please fix the issues")
        lines.append(f"Stage: {failure_brief.stage}")
        if failure_brief.command:
            lines.append(f"Command: {failure_brief.command}")
        if failure_brief.exit_code is not None:
            lines.append(f"Exit code: {failure_brief.exit_code}")
        lines.append(f"Error excerpt:\n{failure_brief.primary_error_excerpt}")
        lines.append(f"Reminder: {failure_brief.constraints_reminder}")
        lines.append("")

    # --- Output format ---
    lines.append("## Required Output Format (STRICT — no deviations)")
    lines.append("Output ONLY a single JSON object with exactly two keys:")
    lines.append('  "summary"  — a brief description of what you changed')
    lines.append('  "writes"   — an array of objects, each with:')
    lines.append('      "path"        — relative file path (must be in allowed files)')
    lines.append(
        '      "base_sha256" — hex SHA256 of the file\'s current content '
        "(from the sha256 values shown above)"
    )
    lines.append('      "content"     — the COMPLETE new file content as a string')
    lines.append("")
    lines.append("Do NOT wrap the JSON in markdown fences or add any other text.")
    lines.append("Every write must contain the FULL file content, not a partial edit.")

    return "\n".join(lines)


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

    attempt_dir = os.path.join(out_dir, run_id, f"attempt_{attempt_index}")
    os.makedirs(attempt_dir, exist_ok=True)

    # Failure brief from a prior attempt (if retrying)
    prev_fb: FailureBrief | None = None
    if state.get("failure_brief"):
        prev_fb = FailureBrief(**state["failure_brief"])

    # Read context
    context_files = _read_context_files(work_order, repo_root)

    # Build prompt
    prompt = _build_prompt(work_order, context_files, prev_fb)

    # Persist the full prompt for post-mortem auditability
    prompt_path = os.path.join(attempt_dir, "se_prompt.txt")
    with open(prompt_path, "w", encoding="utf-8") as fh:
        fh.write(prompt)

    # --- Call LLM ---
    try:
        raw = llm.complete(
            prompt=prompt,
            model=state["llm_model"],
            temperature=state["llm_temperature"],
        )
    except Exception as exc:
        fb = FailureBrief(
            stage="exception",
            primary_error_excerpt=truncate(str(exc)),
            constraints_reminder="LLM API call failed. Check OPENAI_API_KEY and model name.",
        )
        save_json(fb.model_dump(), os.path.join(attempt_dir, "failure_brief.json"))
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
            os.path.join(attempt_dir, "raw_llm_response.json"),
        )
        save_json(fb.model_dump(), os.path.join(attempt_dir, "failure_brief.json"))
        return {"proposal": None, "write_ok": False, "failure_brief": fb.model_dump()}

    # Save proposal artifact
    save_json(
        proposal.model_dump(), os.path.join(attempt_dir, "proposed_writes.json")
    )

    return {"proposal": proposal.model_dump(), "write_ok": False, "failure_brief": None}
