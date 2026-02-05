import os
import shutil
import ast
import sys
import subprocess
from pathlib import Path
import difflib
from .schemas import (
    AppliedChange,
    CommandResult,
    CommandSpec,
    SEPacket,
    ToolReport,
    WorkOrder,
)
from .util import command_to_argv, matches_any_glob, normalize_rel_path, safe_join

import re as _re

_OVERFIT_MARKERS = [
    _re.compile(r"\bworkaround\b", _re.IGNORECASE),
    _re.compile(r"\bspecific test\b", _re.IGNORECASE),
    _re.compile(r"\btest case\b", _re.IGNORECASE),
    _re.compile(r"\bif\s+text\s*==", _re.IGNORECASE),  # hardcoded input check
]

def _looks_like_overfit_hack(src: str) -> bool:
    return any(rx.search(src) for rx in _OVERFIT_MARKERS)

def _normalize_command_spec(item, default_timeout: int) -> CommandSpec:

    if isinstance(item, str):
        return CommandSpec(argv=command_to_argv(item), shell=False, timeout_sec=default_timeout)

    spec = CommandSpec.model_validate(item)
    if spec.timeout_sec is None:
        spec.timeout_sec = default_timeout
    return spec


def _stdlib_only_required(wo: WorkOrder) -> bool:
    # Heuristic trigger: you can refine this later.
    text = f"{wo.notes or ''}".lower()
    return ("stdlib" in text) or ("standard library" in text) or ("no external" in text)

def _local_modules(repo_root: Path) -> set[str]:
    # Treat top-level .py files as allowable local imports (slugify.py -> slugify)
    mods = set()
    for p in repo_root.glob("*.py"):
        mods.add(p.stem)
    return mods

def _find_nonstdlib_imports(py_src: str, allowed_local: set[str]) -> list[str]:
    bad = []
    try:
        tree = ast.parse(py_src)
    except SyntaxError:
        return bad  # Let the acceptance tests deal with syntax errors.
    stdlib = getattr(sys, "stdlib_module_names", set())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = (alias.name or "").split(".")[0]
                if name and name not in stdlib and name not in allowed_local:
                    bad.append(name)
        elif isinstance(node, ast.ImportFrom):
            name = (node.module or "").split(".")[0]
            if name and name not in stdlib and name not in allowed_local:
                bad.append(name)
    return sorted(set(bad))



def _estimate_changed_lines(old: str, new: str) -> int:
    """
    Estimate 'how big' a change is in lines, using SequenceMatcher opcodes.
    Counts the max of removed/added lines for each non-equal block.
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    changed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        changed += max(i2 - i1, j2 - j1)
    return changed


def _read_text_best_effort(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None



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

    # ---------------------------------------------------------------------
    # Stdlib-only import gating (deterministic)
    # ---------------------------------------------------------------------
    if _stdlib_only_required(wo):
        allowed_local = _local_modules(repo_root)
        for w, rel, abs_path in validated_writes:
            if rel.endswith(".py") and w.mode != "delete":
                bad = _find_nonstdlib_imports(w.content or "", allowed_local)
                if bad:
                    blocked.append(f"{rel} (policy) non-stdlib imports not allowed: {bad}")


    # ---------------------------------------------------------------------
    # Overfit-hack gating (heuristic but very effective)
    # ---------------------------------------------------------------------
    for w, rel, abs_path in validated_writes:
        if w.mode != "delete" and rel.endswith(".py"):
            if _looks_like_overfit_hack(w.content or ""):
                blocked.append(f"{rel} (policy) suspected overfit/single-example hack; propose a general rule.")


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
            commands_ran=False,
        )
        return {"tool_report": report.model_dump()}



    # ---------------------------------------------------------------------
    # Rewrite budget / diff gating (deterministic)
    # ---------------------------------------------------------------------
    max_files_changed = getattr(wo, "max_files_changed", None)
    max_changed_lines_per_file = getattr(wo, "max_changed_lines_per_file", None)
    max_total_changed_lines = getattr(wo, "max_total_changed_lines", None)
    max_bytes_per_file = getattr(wo, "max_bytes_per_file", None)

    # Count non-delete writes as "files changed"
    touched_files = [t for t in validated_writes if t[0].mode != "delete"]
    if max_files_changed is not None and len(touched_files) > max_files_changed:
        blocked.append(f"(policy) Too many files changed: {len(touched_files)} > {max_files_changed}")

    total_changed_lines = 0

    for w, rel, abs_path in validated_writes:
        if w.mode == "delete":
            continue

        new_content = w.content or ""

        if max_bytes_per_file is not None:
            new_bytes = len(new_content.encode("utf-8", errors="ignore"))
            if new_bytes > max_bytes_per_file:
                blocked.append(f"{rel} (policy) new content too large: {new_bytes} > {max_bytes_per_file}")
                continue

        # Only diff-gate if the file exists and is readable as text.
        if abs_path.exists() and abs_path.is_file():
            old_content = _read_text_best_effort(abs_path)
            if old_content is None:
                blocked.append(f"{rel} (policy) could not read existing file for diff gating")
                continue

            changed_lines = _estimate_changed_lines(old_content, new_content)
            total_changed_lines += changed_lines

            if max_changed_lines_per_file is not None and changed_lines > max_changed_lines_per_file:
                blocked.append(f"{rel} (policy) diff too large: {changed_lines} > {max_changed_lines_per_file}")

    if max_total_changed_lines is not None and total_changed_lines > max_total_changed_lines:
        blocked.append(f"(policy) total diff too large: {total_changed_lines} > {max_total_changed_lines}")

    if blocked:
        report = ToolReport(
            applied=[],
            blocked_writes=blocked,
            command_results=[],
            all_commands_ok=False,
            commands_ran=False,
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
        commands_ran=True,
    )
    return {"tool_report": report.model_dump()}
