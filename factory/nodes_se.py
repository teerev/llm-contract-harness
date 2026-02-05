from pathlib import Path
from .schemas import SEPacket
from .util import strict_json_loads


def _format_constraints(wo: dict) -> str:
    """Format only the non-empty constraint fields from work order."""
    lines = []
    
    if wo.get("forbidden_paths"):
        lines.append(f"forbidden_paths: {wo['forbidden_paths']}")
    if wo.get("allowed_paths"):
        lines.append(f"allowed_paths: {wo['allowed_paths']}")
    if wo.get("notes"):
        lines.append(f"notes: {wo['notes']}")
    
    return "\n".join(lines) if lines else ""


def _format_acceptance_commands(wo: dict) -> str:
    """Format acceptance commands cleanly, one per line."""
    cmds = wo.get("acceptance_commands", [])
    if not cmds:
        return ""
    
    lines = []
    for i, cmd in enumerate(cmds, 1):
        if isinstance(cmd, str):
            lines.append(f"  {i}. {cmd}")
        elif isinstance(cmd, dict):
            # CommandSpec format
            cmd_str = cmd.get("cmd") or " ".join(cmd.get("argv", []))
            lines.append(f"  {i}. {cmd_str}")
    
    return "\n".join(lines)


# directories and files to exclude from directory listing
# exact matches for these directory names
EXCLUDE_PATTERNS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}
# suffix patterns (e.g., mypackage.egg-info)
EXCLUDE_SUFFIXES = {".egg-info"}


def _should_include_in_tree(path: Path) -> bool:
    """returns true if this path should appear in the tree listing."""
    for part in path.parts:
        if part in EXCLUDE_PATTERNS:
            return False
        if any(part.endswith(suffix) for suffix in EXCLUDE_SUFFIXES):
            return False
    return True


def _build_file_context(repo_path: Path, context_files: list[str]) -> str:
    """
    build file context string for se prompt.

    includes:
    1. directory tree (always) - so se knows what files exist
    2. contents of files matching context_files patterns
    """
    repo = Path(repo_path)
    lines = []

    # build directory tree
    lines.append("=== DIRECTORY STRUCTURE ===")
    for path in sorted(repo.rglob("*")):
        if path.is_file() and _should_include_in_tree(path.relative_to(repo)):
            lines.append(f"  {path.relative_to(repo)}")

    # read files matching context_files patterns
    if context_files:
        lines.append("\n=== FILE CONTENTS ===")
        seen = set()
        for pattern in context_files:
            for path in repo.glob(pattern):
                if path.is_file() and path not in seen:
                    seen.add(path)
                    rel_path = path.relative_to(repo)
                    try:
                        content = path.read_text(encoding="utf-8")
                        lines.append(f"\n--- {rel_path} ---")
                        lines.append(content)
                    except Exception as e:
                        lines.append(f"\n--- {rel_path} (read error: {e}) ---")

    return "\n".join(lines)


SE_SYSTEM = """\
You are the Software Engineer (SE).

Acceptance tests are the spec; follow them even if they contradict common conventions.
Do not regress previously passing acceptance commands.

When you see expected=X got=Y, you must change behavior so the same input yields X exactly; do not rationalize the test.

Overfit guardrail (MANDATORY):
- Do NOT introduce single-example hacks, such as:
  - hardcoding a specific input/output pair (e.g., `if text == "...": return "..."`)
  - globally deleting/replacing a common character or token to satisfy one failing case
- Prefer a general rule consistent with ALL acceptance tests.
- If only a small number of commands fail, make the smallest/localest change you can to fix them while preserving the behavior that keeps the passed commands passing.


Goal: propose the MINIMAL set of repo file changes to satisfy the work order.

Output contract (MANDATORY):
- Output MUST be a single JSON object matching:
  {
    "summary": "string",
    "writes": [{"path":"relative/path","content":"...","mode":"create|replace|delete"}, ...],
    "assumptions": ["...", ...]
  }
- No markdown. No code fences. Only JSON.

Constraints:
- Never use absolute paths or '..' segments.
- Do not modify files matching forbidden_paths.
- If allowed_paths is non-empty, only write within allowed_paths.
- Prefer smallest diffs: do not refactor unrelated code.
"""


def make_se_node(model):
    """
    Factory function that creates an SE (Software Engineer) node with an injected LLM.

    LangGraph requires all nodes to have the signature (state: dict) -> dict, with no
    additional parameters. This factory uses a closure to "bake in" the model dependency:
    the returned se_node function captures `model` from this enclosing scope, allowing it
    to use the LLM while still conforming to LangGraph's required signature.

    Args:
        model: The LLM instance to use for generating code change proposals.

    Returns:
        A node function (state: dict) -> dict that can be registered with StateGraph.add_node().
    """

    def se_node(state: dict) -> dict:
        wo = state["work_order"]
        body = state["work_order_body"]
        repo_path = state["repo_path"]
        iteration = int(state.get("iteration", 0))

        # build file context from workspace
        context_files = wo.get("context_files", [])

        # get applied changes from tool report and include them in the file context 
        applied = (state.get("tool_report") or {}).get("applied") or []
        applied_paths = []
        for a in applied:
            p = a.get("path")
            if isinstance(p, str) and p:
                applied_paths.append(p)

        # include those files explicitly
        context_files = list(dict.fromkeys(context_files + applied_paths))

        file_context = _build_file_context(Path(repo_path), context_files)

        # format constraints (only non-empty ones)
        constraints = _format_constraints(wo)
        constraints_section = f"\nConstraints:\n{constraints}" if constraints else ""

        # format acceptance commands
        acceptance = _format_acceptance_commands(wo)
        acceptance_section = f"\n\n=== ACCEPTANCE TESTS ===\n{acceptance}" if acceptance else ""

        # format previous failure feedback prominently
        prior = ""
        po = state.get("po_report") or {}
        fixes = po.get("required_fixes") or []
        if fixes:
            prior = "\n\n=== PREVIOUS FAILURE (MUST FIX) ===\n" + "\n\n".join(fixes)

        user = f"""\
=== TASK ===
{body.strip()}
{constraints_section}{acceptance_section}

=== CURRENT REPO ===
{file_context}

=== ITERATION {iteration} ==={prior}
"""
        wo_title = wo.get("title", "")
        raw = model.complete(system=SE_SYSTEM, user=user, iteration=iteration, work_order_name=wo_title)

        try:
            data = strict_json_loads(raw)
            pkt = SEPacket.model_validate(data)
        except Exception as e:
            pkt = SEPacket(
                summary="Invalid SE JSON; emitting no-op.",
                writes=[],
                assumptions=[f"Parse error: {type(e).__name__}: {e}"],
            )

        return {"se_packet": pkt.model_dump()}

    return se_node
