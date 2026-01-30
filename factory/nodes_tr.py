import os
import shutil
import subprocess
from pathlib import Path
from .schemas import (
    AppliedChange,
    CommandResult,
    CommandSpec,
    SEPacket,
    ToolReport,
    WorkOrder,
)
from .util import command_to_argv, matches_any_glob, normalize_rel_path, safe_join


def _normalize_command_spec(item, default_timeout: int) -> CommandSpec:

    if isinstance(item, str):
        return CommandSpec(argv=command_to_argv(item), shell=False, timeout_sec=default_timeout)

    spec = CommandSpec.model_validate(item)
    if spec.timeout_sec is None:
        spec.timeout_sec = default_timeout
    return spec


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
        spec = _normalize_command_spec(item, wo.command_timeout_sec)
        r = run_command(spec, cwd=repo_root, env=env)
        results.append(r)
        if r.returncode != 0:
            all_ok = False

    report = ToolReport(
        applied=applied,
        blocked_writes=blocked,
        command_results=results,
        all_commands_ok=all_ok,
    )
    return {"tool_report": report.model_dump()}
