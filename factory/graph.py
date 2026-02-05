from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from factory.nodes_po import po_node
from factory.nodes_se import se_node
from factory.nodes_tr import tr_node
from factory.schemas import AttemptRecord, FailureBrief, PatchProposal, WorkOrder


class FactoryState(TypedDict, total=False):
    repo_root: str
    out_dir: str
    run_dir: str
    run_id: str
    work_order: WorkOrder
    work_order_path: str
    attempt_index: int
    max_attempts: int
    baseline_commit: str
    timeout_seconds: int

    llm: Any

    patch_proposal: PatchProposal | None
    patch_path: str
    touched_files: list[str]
    apply_ok: bool
    failure_brief: FailureBrief | None

    attempt_records: list[AttemptRecord]
    verdict: str
    ended_stage: str


def _route_after_po(state: FactoryState) -> str:
    if state.get("verdict") == "PASS":
        return "end"
    attempts = state.get("attempt_records") or []
    max_attempts = int(state.get("max_attempts") or 0)
    if len(attempts) < max_attempts:
        return "se"
    return "end"


def build_graph():
    g: StateGraph = StateGraph(FactoryState)  # type: ignore[arg-type]
    g.add_node("SE", se_node)
    g.add_node("TR", tr_node)
    g.add_node("PO", po_node)

    g.set_entry_point("SE")
    g.add_edge("SE", "TR")
    g.add_edge("TR", "PO")
    g.add_conditional_edges("PO", _route_after_po, {"se": "SE", "end": END})
    return g.compile()

