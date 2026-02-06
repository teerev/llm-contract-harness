"""LangGraph graph definition and routing."""

from __future__ import annotations

import os
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from factory.nodes_po import po_node
from factory.nodes_se import se_node
from factory.nodes_tr import tr_node
from factory.util import save_json
from factory.workspace import get_tree_hash, rollback


# ---------------------------------------------------------------------------
# State schema — each key becomes its own LastValue channel so partial
# node returns only overwrite the keys they explicitly include.
# ---------------------------------------------------------------------------


class FactoryState(TypedDict, total=False):
    # Configuration (set once at start)
    work_order: dict
    repo_root: str
    baseline_commit: str
    max_attempts: int
    timeout_seconds: int
    llm_model: str
    llm_temperature: float
    out_dir: str
    run_id: str
    # Per-attempt (reset by finalize between attempts)
    attempt_index: int
    proposal: Any          # dict | None
    touched_files: list
    write_ok: bool
    failure_brief: Any     # dict | None
    verify_results: list
    acceptance_results: list
    # Accumulated across attempts
    attempts: list
    verdict: str
    repo_tree_hash_after: Any  # str | None


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


def _route_after_se(state: dict) -> str:
    """After SE: proceed to TR if a proposal was produced, else finalize."""
    if state.get("failure_brief") is not None:
        return "finalize"
    return "tr"


def _route_after_tr(state: dict) -> str:
    """After TR: proceed to PO if writes applied, else finalize."""
    if state.get("failure_brief") is not None:
        return "finalize"
    return "po"


def _route_after_finalize(state: dict) -> str:
    """After finalize: END on PASS or exhausted attempts, else retry from SE."""
    if state.get("verdict") == "PASS":
        return END
    if state["attempt_index"] > state["max_attempts"]:
        return END
    return "se"


# ---------------------------------------------------------------------------
# Finalize node
# ---------------------------------------------------------------------------


def _finalize_node(state: dict) -> dict:
    """Record the attempt, rollback on failure, and prepare for retry or end."""
    attempt_index: int = state["attempt_index"]
    out_dir: str = state["out_dir"]
    run_id: str = state["run_id"]
    repo_root: str = state["repo_root"]
    baseline: str = state["baseline_commit"]

    attempt_dir = os.path.join(out_dir, run_id, f"attempt_{attempt_index}")
    os.makedirs(attempt_dir, exist_ok=True)

    failure_brief = state.get("failure_brief")

    # --- Determine verdict for this attempt ---
    verdict: str = "FAIL" if failure_brief else "PASS"

    # --- Proposal path ---
    proposal_path = os.path.join(attempt_dir, "proposed_writes.json")
    if not os.path.exists(proposal_path):
        proposal_path = ""

    # --- Save failure_brief artifact ---
    if failure_brief:
        save_json(failure_brief, os.path.join(attempt_dir, "failure_brief.json"))

    # --- Build attempt record ---
    attempt_record: dict = {
        "attempt_index": attempt_index,
        "baseline_commit": baseline,
        "proposal_path": proposal_path,
        "touched_files": list(state.get("touched_files") or []),
        "write_ok": state.get("write_ok", False),
        "verify": list(state.get("verify_results") or []),
        "acceptance": list(state.get("acceptance_results") or []),
        "failure_brief": failure_brief,
    }

    attempts = list(state.get("attempts") or [])
    attempts.append(attempt_record)

    # --- Rollback on failure (safe even when no writes were applied) ---
    repo_tree_hash_after: Optional[str] = None
    if verdict == "FAIL":
        rollback(repo_root, baseline)
    else:
        # PASS → stage only proposal-touched files, then compute tree hash.
        # Scoping prevents verification artifacts from polluting the hash.
        touched = list(state.get("touched_files") or [])
        repo_tree_hash_after = get_tree_hash(repo_root, touched_files=touched or None)

    return {
        "attempts": attempts,
        "attempt_index": attempt_index + 1,
        # Reset per-attempt fields
        "proposal": None,
        "touched_files": [],
        "write_ok": False,
        # Keep failure_brief so SE can read it on the retry prompt
        "failure_brief": failure_brief,
        "verify_results": [],
        "acceptance_results": [],
        # Verdict / tree hash
        "verdict": verdict,
        "repo_tree_hash_after": repo_tree_hash_after,
    }


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graph():
    """Construct and compile the LangGraph factory graph (SE → TR → PO loop)."""
    graph = StateGraph(FactoryState)

    graph.add_node("se", se_node)
    graph.add_node("tr", tr_node)
    graph.add_node("po", po_node)
    graph.add_node("finalize", _finalize_node)

    graph.set_entry_point("se")

    graph.add_conditional_edges(
        "se", _route_after_se, {"tr": "tr", "finalize": "finalize"}
    )
    graph.add_conditional_edges(
        "tr", _route_after_tr, {"po": "po", "finalize": "finalize"}
    )
    graph.add_edge("po", "finalize")
    graph.add_conditional_edges(
        "finalize", _route_after_finalize, {"se": "se", END: END}
    )

    return graph.compile()
