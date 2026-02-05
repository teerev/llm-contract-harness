from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.llm import LLMClient, parse_patch_proposal
from factory.schemas import FailureBrief, PatchProposal, WorkOrder
from factory.util import bounded_read_text_files, truncate_output, utc_now_iso


CONSTRAINTS_REMINDER = (
    "Return ONLY valid JSON with keys {unified_diff, summary} and no extra keys. "
    "No markdown/code fences. unified_diff must be a unified diff with git headers "
    "and must only touch allowed_files."
)


def _format_failure_brief_for_prompt(fb: FailureBrief) -> str:
    parts: list[str] = [
        "Previous attempt failure:",
        f"- stage: {fb.stage}",
    ]
    if fb.command is not None:
        parts.append(f"- command: {fb.command}")
    if fb.exit_code is not None:
        parts.append(f"- exit_code: {fb.exit_code}")
    parts.append("- primary_error_excerpt:")
    parts.append(fb.primary_error_excerpt)
    parts.append("- constraints_reminder:")
    parts.append(fb.constraints_reminder)
    return "\n".join(parts)


def build_se_prompt(
    *,
    work_order: WorkOrder,
    repo_root: Path,
    failure_brief: FailureBrief | None,
    max_context_total_bytes: int = 200_000,
) -> str:
    ctx = bounded_read_text_files(
        repo_root, work_order.context_files, max_total_bytes=max_context_total_bytes
    )

    lines: list[str] = []
    lines.append("You are SE (Software Engineer) in a strict factory harness.")
    lines.append(CONSTRAINTS_REMINDER)
    lines.append("")
    lines.append("## WorkOrder")
    lines.append(f"id: {work_order.id}")
    lines.append(f"title: {work_order.title}")
    lines.append("intent:")
    lines.append(work_order.intent)
    if work_order.notes:
        lines.append("notes:")
        lines.append(work_order.notes)
    lines.append("")
    lines.append("## Constraints")
    lines.append("allowed_files (relative paths; you may only touch these):")
    for p in work_order.allowed_files:
        lines.append(f"- {p}")
    lines.append("")
    lines.append("forbidden:")
    for f in work_order.forbidden:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("acceptance_commands (must pass after global verify):")
    for c in work_order.acceptance_commands:
        lines.append(f"- {c}")
    lines.append("")
    if failure_brief is not None:
        lines.append("## FailureBrief")
        lines.append(_format_failure_brief_for_prompt(failure_brief))
        lines.append("")
    lines.append("## Context files (bounded, may be truncated)")
    for rel, content in ctx:
        lines.append(f"### {rel}")
        lines.append(content)
        if not content.endswith("\n"):
            lines.append("")
    lines.append("")
    lines.append("## Output format (STRICT)")
    lines.append(
        'Return ONLY a JSON object with keys "unified_diff" and "summary". '
        "Do not include any other keys, and do not wrap in markdown."
    )
    lines.append(
        '"unified_diff" must be directly applyable by `git apply --whitespace=nowarn`.'
    )
    return "\n".join(lines).strip() + "\n"


def se_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    LangGraph node: produce PatchProposal (or FailureBrief on invalid output).
    Writes proposed_patch.diff under attempt directory.
    """
    work_order: WorkOrder = state["work_order"]
    repo_root = Path(state["repo_root"])
    run_dir = Path(state["run_dir"])
    attempt_index = int(state["attempt_index"])
    attempt_dir = run_dir / f"attempt_{attempt_index}"
    attempt_dir.mkdir(parents=True, exist_ok=True)

    patch_path = attempt_dir / "proposed_patch.diff"
    llm: LLMClient = state["llm"]
    prior_failure: FailureBrief | None = state.get("failure_brief")

    prompt = build_se_prompt(
        work_order=work_order, repo_root=repo_root, failure_brief=prior_failure
    )

    raw: str = ""
    try:
        raw = llm.complete(prompt)
        proposal: PatchProposal = parse_patch_proposal(raw)
        patch_path.write_text(proposal.unified_diff, encoding="utf-8")
        return {
            "patch_proposal": proposal,
            "patch_path": str(patch_path),
            "failure_brief": None,
            "se_completed_utc": utc_now_iso(),
        }
    except Exception as e:
        # Strict failure: invalid JSON, schema mismatch, missing diff headers, or API error.
        excerpt = truncate_output(raw) if raw else str(e)
        fb = FailureBrief(
            stage="llm_output_invalid",
            command=None,
            exit_code=None,
            primary_error_excerpt=truncate_output(f"{e}\n\n{excerpt}").strip(),
            constraints_reminder=CONSTRAINTS_REMINDER,
        )
        patch_path.write_text("", encoding="utf-8")
        return {
            "patch_proposal": None,
            "patch_path": str(patch_path),
            "failure_brief": fb,
            "se_completed_utc": utc_now_iso(),
        }

