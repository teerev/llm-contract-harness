import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from .schemas import (
    AcceptanceCheck,
    AppliedChange,
    CommandResult,
    CommandSpec,
    SEPacket,
    ShellPolicy,
    ToolReport,
    WorkOrder,
)
from .invariants import run_invariants
from .util import command_to_argv, matches_any_glob, normalize_rel_path, safe_join

logger = logging.getLogger(__name__)


def _normalize_command_spec(item, default_timeout: int) -> CommandSpec:

    if isinstance(item, str):
        return CommandSpec(argv=command_to_argv(item), shell=False, timeout_sec=default_timeout)

    spec = CommandSpec.model_validate(item)
    if spec.timeout_sec is None:
        spec.timeout_sec = default_timeout
    return spec


def _enforce_shell_policy(spec: CommandSpec, policy: ShellPolicy) -> tuple[bool, str]:
    """
    Enforce shell policy for a command.
    
    This is a critical security control that prevents shell injection attacks.
    When shell=True is used, the command string is passed to the shell interpreter,
    which allows shell metacharacters (|, ;, &&, $(), etc.) to be interpreted.
    This can lead to command injection if the command contains user-controlled input.
    
    Args:
        spec: The command specification to check
        policy: One of "forbidden", "warn", or "allow"
        
    Returns:
        Tuple of (allowed, reason). If not allowed, reason explains why.
    """
    if not spec.shell:
        return True, ""
    
    cmd_preview = (spec.cmd or "")[:100]
    
    if policy == "forbidden":
        return False, f"shell=True is forbidden by shell_policy. Command: {cmd_preview}"
    elif policy == "warn":
        logger.warning(f"shell=True used (policy=warn): {cmd_preview}")
        return True, ""
    else:  # allow
        return True, ""


def _check_assertions(result: CommandResult, check: AcceptanceCheck) -> tuple[bool, list[str]]:
    """
    Check structured assertions against command result (M10).
    
    This enables verification of command output content, not just return code.
    Prevents SE from gaming acceptance by writing scripts that just `exit 0`.
    
    Args:
        result: The command execution result
        check: The AcceptanceCheck with assertion criteria
        
    Returns:
        Tuple of (all_passed, list of failure messages)
    """
    failures: list[str] = []
    
    # Returncode check
    if result.returncode != check.expected_returncode:
        failures.append(
            f"Expected returncode {check.expected_returncode}, got {result.returncode}"
        )
    
    # stdout_contains - all strings must appear
    if check.stdout_contains:
        for expected in check.stdout_contains:
            if expected not in result.stdout:
                failures.append(f"stdout missing expected: '{expected}'")
    
    # stdout_not_contains - none of these strings should appear
    if check.stdout_not_contains:
        for forbidden in check.stdout_not_contains:
            if forbidden in result.stdout:
                failures.append(f"stdout contains forbidden: '{forbidden}'")
    
    # stdout_regex - stdout must match the pattern
    if check.stdout_regex:
        if not re.search(check.stdout_regex, result.stdout):
            failures.append(f"stdout doesn't match regex: '{check.stdout_regex}'")
    
    # stderr_must_be_empty - stderr must be empty (after stripping whitespace)
    if check.stderr_must_be_empty and result.stderr.strip():
        stderr_preview = result.stderr[:100].replace('\n', '\\n')
        failures.append(f"stderr must be empty but got: '{stderr_preview}'")
    
    # stderr_contains - all strings must appear in stderr
    if check.stderr_contains:
        for expected in check.stderr_contains:
            if expected not in result.stderr:
                failures.append(f"stderr missing expected: '{expected}'")
    
    return len(failures) == 0, failures


def run_command(spec: CommandSpec, cwd: Path, env: dict[str, str]) -> CommandResult:
    
    try:
        if spec.shell:
            p = subprocess.run(
                spec.cmd or "",
                shell=True,
                cwd=str(cwd),
                text=True,
                capture_output=True,
                env=env,
                timeout=spec.timeout_sec,
            )
        else:
            argv = spec.argv or (command_to_argv(spec.cmd or "") if spec.cmd else [])
            p = subprocess.run(
                argv,
                shell=False,
                cwd=str(cwd),
                text=True,
                capture_output=True,
                env=env,
                timeout=spec.timeout_sec,
            )
        return CommandResult(
            spec=spec.model_dump(),
            returncode=int(p.returncode),
            stdout=p.stdout or "",
            stderr=p.stderr or "",
            timed_out=False,
        )
    except subprocess.TimeoutExpired as te:
        return CommandResult(
            spec=spec.model_dump(),
            returncode=124,
            stdout=(te.stdout or "") if isinstance(te.stdout, str) else "",
            stderr=(te.stderr or "") if isinstance(te.stderr, str) else "Timed out",
            timed_out=True,
        )
    except FileNotFoundError as e:
        return CommandResult(
            spec=spec.model_dump(),
            returncode=127,
            stdout="",
            stderr=f"Command not found: {e}",
            timed_out=False,
        )


def tool_runner_node(state: dict) -> dict:

    repo_root = Path(state["repo_path"]).resolve()
    wo = WorkOrder.model_validate(state["work_order"])
    pkt = SEPacket.model_validate(state["se_packet"])

    applied: list[AppliedChange] = []
    blocked: list[str] = []

    validated_writes: list[tuple] = []  # (write, rel_path, abs_path)

    for w in pkt.writes:
        rel = normalize_rel_path(w.path)

        if matches_any_glob(rel, wo.forbidden_paths):
            blocked.append(rel)
            continue
        if wo.allowed_paths and (not matches_any_glob(rel, wo.allowed_paths)):
            blocked.append(rel)
            continue

        try:
            abs_path = safe_join(repo_root, rel)
        except Exception:
            blocked.append(rel)
            continue

        validated_writes.append((w, rel, abs_path))

    if blocked:
        report = ToolReport(
            applied=[],
            blocked_writes=blocked,
            command_results=[],
            all_commands_ok=False,
        )
        return {"tool_report": report.model_dump()}

    for w, rel, abs_path in validated_writes:
        if w.mode == "delete":
            if abs_path.exists():
                if abs_path.is_dir():
                    shutil.rmtree(abs_path)
                else:
                    abs_path.unlink()
            applied.append(AppliedChange(path=rel, action="delete"))
            continue

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(w.content or "", encoding="utf-8")
        applied.append(AppliedChange(path=rel, action="create" if w.mode == "create" else "replace"))

    env = {**os.environ, **wo.env}
    results: list[CommandResult] = []
    all_ok = True

    for item in wo.acceptance_commands:
        # M9/M10: Handle AcceptanceCheck with structured assertions
        acceptance_check: AcceptanceCheck | None = None
        
        if isinstance(item, dict) and "command" in item:
            # This is an AcceptanceCheck (has 'command' field, not 'cmd' or 'argv')
            acceptance_check = AcceptanceCheck.model_validate(item)
            # Extract the command to run
            cmd_item = acceptance_check.command
            timeout = acceptance_check.timeout_sec or wo.command_timeout_sec
            spec = _normalize_command_spec(cmd_item, timeout)
        elif isinstance(item, AcceptanceCheck):
            acceptance_check = item
            cmd_item = acceptance_check.command
            timeout = acceptance_check.timeout_sec or wo.command_timeout_sec
            spec = _normalize_command_spec(cmd_item, timeout)
        else:
            # Regular command (string or CommandSpec)
            spec = _normalize_command_spec(item, wo.command_timeout_sec)
        
        # M7: Enforce shell policy before running command
        allowed, reason = _enforce_shell_policy(spec, wo.shell_policy)
        if not allowed:
            results.append(CommandResult(
                spec=spec.model_dump(),
                returncode=1,
                stdout="",
                stderr=reason,
                timed_out=False,
            ))
            all_ok = False
            continue
        
        r = run_command(spec, cwd=repo_root, env=env)
        
        # M10: Check structured assertions if this is an AcceptanceCheck
        if acceptance_check is not None:
            passed, failures = _check_assertions(r, acceptance_check)
            if not passed:
                # Append assertion failures to stderr for visibility
                assertion_msg = "Assertion failures:\n- " + "\n- ".join(failures)
                r = CommandResult(
                    spec=r.spec,
                    returncode=r.returncode if r.returncode != 0 else 1,  # Force failure
                    stdout=r.stdout,
                    stderr=(r.stderr + "\n" + assertion_msg).strip(),
                    timed_out=r.timed_out,
                )
                all_ok = False
            elif r.returncode != 0:
                all_ok = False
        else:
            # Regular command - just check returncode
            if r.returncode != 0:
                all_ok = False
        
        results.append(r)

    # Run invariant checks (Layer 2 verification)
    inv_report = run_invariants(
        workspace=repo_root,
        se_packet=pkt.model_dump(),
        work_order=wo.model_dump(),
    )

    report = ToolReport(
        applied=applied,
        blocked_writes=blocked,
        command_results=results,
        all_commands_ok=all_ok,
        invariant_report=inv_report,
        all_invariants_ok=inv_report.all_passed,
    )
    return {"tool_report": report.model_dump()}
