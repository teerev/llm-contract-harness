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

    decision = "PASS" if (not reasons and tr.all_commands_ok) else "FAIL"
    if decision == "PASS":
        reasons.append("All acceptance commands passed and no constraints were violated.")

    report = POReport(decision=decision, reasons=reasons, required_fixes=fixes)


    it = int(state.get("iteration", 0)) + 1
    return {"po_report": report.model_dump(), "iteration": it}
