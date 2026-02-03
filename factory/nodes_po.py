from .schemas import POReport, ToolReport


def po_node(state: dict) -> dict:
    tr = ToolReport.model_validate(state["tool_report"])

    reasons: list[str] = []
    fixes: list[str] = []

    if tr.blocked_writes:
        reasons.append("Some writes were blocked by path constraints.")
        fixes.append(f"Remove/avoid these paths: {tr.blocked_writes}")

    if not tr.all_commands_ok:
        reasons.append("One or more acceptance commands failed.")
        for cr in tr.command_results:
            if cr.returncode != 0:
                fixes.append(
                    f"Fix failing command (exit={cr.returncode}, timed_out={cr.timed_out}): {cr.spec}"
                )

    # Check invariants (Layer 2 verification)
    if not tr.all_invariants_ok and tr.invariant_report:
        reasons.append("One or more invariant checks failed.")
        for inv in tr.invariant_report.results:
            if not inv.passed:
                fixes.append(f"Fix invariant '{inv.check_name}': {inv.message}")

    decision = "PASS" if (not reasons and tr.all_commands_ok and tr.all_invariants_ok) else "FAIL"
    if decision == "PASS":
        reasons.append("All acceptance commands passed, invariants satisfied, and no constraints were violated.")

    report = POReport(decision=decision, reasons=reasons, required_fixes=fixes)

    it = int(state.get("iteration", 0)) + 1
    return {"po_report": report.model_dump(), "iteration": it}
