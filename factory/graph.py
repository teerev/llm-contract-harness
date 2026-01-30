from typing import Any, TypedDict
from langgraph.graph import END, START, StateGraph
from .nodes_po import po_node
from .nodes_se import make_se_node
from .nodes_tr import tool_runner_node


class PrototypeState(TypedDict, total=False):
    # inputs
    repo_path: str  # workspace repo root (where TR runs commands)
    work_order: dict[str, Any]
    work_order_body: str

    # loop control
    iteration: int
    max_iterations: int

    # artifacts from nodes
    se_packet: dict[str, Any]
    tool_report: dict[str, Any]
    po_report: dict[str, Any]


def build_graph(se_model):

    g = StateGraph(PrototypeState)

    g.add_node("SE", make_se_node(se_model))
    g.add_node("TR", tool_runner_node)
    g.add_node("PO", po_node)

    g.add_edge(START, "SE")
    g.add_edge("SE", "TR")
    g.add_edge("TR", "PO")

    g.add_conditional_edges(source="PO", path=conditional_route, path_map={"SE": "SE", END: END})
    return g.compile()


def conditional_route(state: dict):

    po = state.get("po_report") or {}
    if po.get("decision") == "PASS":
        return END
    it = int(state.get("iteration", 0))
    max_it = int(state.get("max_iterations", 5))
    if it >= max_it:
        return END
    return "SE"
