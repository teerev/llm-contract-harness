from pathlib import Path
from .schemas import SEPacket
from .util import strict_json_loads

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

        prior = ""
        po = state.get("po_report") or {}
        fixes = po.get("required_fixes") or []
        if fixes:
            prior = "\n\nPrevious FAIL required_fixes:\n- " + "\n- ".join(fixes)

        # build file context from workspace
        context_files = wo.get("context_files", [])
        file_context = _build_file_context(Path(repo_path), context_files)

        user = f"""\
work order metadata (dict):
{wo}

work order body:
{body}

target repo (workspace) path:
{repo_path}

current repo state:
{file_context}

iteration: {iteration}{prior}
"""
        raw = model.complete(system=SE_SYSTEM, user=user)

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
