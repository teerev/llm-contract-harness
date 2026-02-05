from .schemas import POReport, ToolReport
import re as _re

_ASSERT_RE = _re.compile(r"AssertionError:\s*Expected\s+(?P<exp>.+?),\s*got\s+(?P<got>.+)$")

def _extract_assert(stderr: str) -> tuple[str | None, str | None]:
    # returns (expected, got) with quotes preserved (good enough)
    for line in reversed(stderr.splitlines()):
        m = _ASSERT_RE.search(line.strip())
        if m:
            return m.group("exp"), m.group("got")
    return None, None

def po_node(state: dict) -> dict:
    tr = ToolReport.model_validate(state["tool_report"])

    reasons: list[str] = []
    fixes: list[str] = []

    if tr.blocked_writes:
        #reasons.append("Some writes were blocked by path constraints.")
        #fixes.append(f"Remove/avoid these paths: {tr.blocked_writes}")
        reasons.append("Some writes were blocked by tool policy (constraints or safety checks).")
        fixes.append(f"Resolve blocked writes: {tr.blocked_writes}")

    elif not tr.commands_ran:
        #reasons.append("Acceptance commands were not executed due to blocked writes.")
        reasons.append("Acceptance commands were not executed.")

    if tr.commands_ran:
        prev_passed = set(state.get("passed_commands") or [])
        passed = []
        failed = []
        for i, cr in enumerate(tr.command_results, 1):
            (passed if cr.returncode == 0 else failed).append(i)

        # This is *huge* for anti-regression pressure.
        fixes.append(f"Passed commands (must stay passing): {passed}")
        fixes.append(f"Failed commands (must fix): {failed}")

        # Explicit regression detection versus previous iteration.
        regressions = sorted(prev_passed - set(passed))
        if regressions:
            reasons.append("Regressions were introduced: previously passing commands are now failing.")
            fixes.append(f"REGRESSIONS introduced (must restore): {regressions}")


    if tr.commands_ran and not tr.all_commands_ok:
        reasons.append("One or more acceptance commands failed.")
        for i, cr in enumerate(tr.command_results, 1):
            if cr.returncode != 0:
                error_detail = f"Fix failing command #{i} (exit={cr.returncode}, timed_out={cr.timed_out}): {cr.spec}"

                exp, got = _extract_assert(cr.stderr or "")
                if exp is not None and got is not None:
                    error_detail += f"\n  MISMATCH: expected={exp} got={got}"
                    # deterministic “hint” without being too magical:
                    # If expected is shorter than got, warn against transliteration.
                    if len(str(exp)) < len(str(got)):
                        error_detail += "\n  HINT: Prefer matching the acceptance test outputs exactly over common conventions. If behavior conflicts with typical implementations, the tests win."

                if cr.stdout.strip():
                    error_detail += f"\n  STDOUT: {cr.stdout.strip()}"
                if cr.stderr.strip():
                    error_detail += f"\n  STDERR: {cr.stderr.strip()}"
                fixes.append(error_detail)

    decision = "PASS" if (not reasons and tr.all_commands_ok) else "FAIL"
    if decision == "PASS":
        reasons.append("All acceptance commands passed and no constraints were violated.")

    report = POReport(decision=decision, reasons=reasons, required_fixes=fixes)
    it = int(state.get("iteration", 0)) + 1
    #return {"po_report": report.model_dump(), "iteration": it}
    out = {"po_report": report.model_dump(), "iteration": it}
    if tr.commands_ran:
        out["passed_commands"] = passed
        out["failed_commands"] = failed
    return out
