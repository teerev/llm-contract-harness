from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from factory.schemas import CmdResult, model_dump, write_pretty_json


EXCLUDE_DIR_NAMES: set[str] = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(obj: Any) -> bytes:
    """
    Canonical JSON bytes: sorted keys, no whitespace, UTF-8 encoding.
    """
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return s.encode("utf-8")


def work_order_hash_from_path(work_order_path: Path) -> str:
    raw = json.loads(work_order_path.read_text(encoding="utf-8"))
    return sha256_hex(canonical_json_bytes(raw))


def config_hash(
    llm_model: str, llm_temperature: float, max_attempts: int, timeout_seconds: int
) -> str:
    s = f"{llm_model}|{llm_temperature}|{max_attempts}|{timeout_seconds}"
    return sha256_hex(s.encode("utf-8"))


def run_id(work_order_hash: str, repo_tree_hash_before: str, config_hash: str) -> str:
    return sha256_hex((work_order_hash + repo_tree_hash_before + config_hash).encode("utf-8"))[
        :12
    ]


def is_path_within(child: Path, parent: Path) -> bool:
    """
    True if child is inside parent (or equal), after resolving symlinks.
    """
    try:
        child_r = child.resolve()
        parent_r = parent.resolve()
    except Exception:
        child_r = child.absolute()
        parent_r = parent.absolute()
    try:
        child_r.relative_to(parent_r)
        return True
    except Exception:
        return False


def stable_repo_tree_hash(repo_root: Path, *, out_dir: Path | None = None) -> str:
    """
    Stable tree hashing:
    - Walk recursively and include regular files only (skip symlinks).
    - Exclude directories listed in EXCLUDE_DIR_NAMES.
    - Exclude out_dir if it is inside repo_root (should not happen per preflight).
    """
    exclude_out_rel: str | None = None
    if out_dir is not None and is_path_within(out_dir, repo_root):
        exclude_out_rel = out_dir.resolve().relative_to(repo_root.resolve()).as_posix().rstrip(
            "/"
        )

    h = hashlib.sha256()
    for root, dirs, files in os.walk(repo_root):
        root_p = Path(root)

        # Prune excluded directories deterministically.
        pruned_dirs: list[str] = []
        for d in dirs:
            if d in EXCLUDE_DIR_NAMES:
                continue
            if exclude_out_rel is not None:
                rel_dir = (root_p / d).resolve().relative_to(repo_root.resolve()).as_posix()
                if rel_dir == exclude_out_rel or rel_dir.startswith(exclude_out_rel + "/"):
                    continue
            pruned_dirs.append(d)
        dirs[:] = sorted(pruned_dirs)

        for fn in sorted(files):
            p = root_p / fn
            try:
                if p.is_symlink():
                    continue
                if not p.is_file():
                    continue
            except Exception:
                continue

            if exclude_out_rel is not None:
                try:
                    relp = p.resolve().relative_to(repo_root.resolve()).as_posix()
                except Exception:
                    relp = p.relative_to(repo_root).as_posix()
                if relp == exclude_out_rel or relp.startswith(exclude_out_rel + "/"):
                    continue
            else:
                relp = p.relative_to(repo_root).as_posix()

            h.update(relp.encode("utf-8"))
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")

    return h.hexdigest()


def truncate_output(text: str, *, max_lines: int = 200, max_chars: int = 8000) -> str:
    """
    Deterministic truncation: last max_lines lines, then last max_chars chars.
    """
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[-max_chars:]
    return out


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename_component(s: str) -> str:
    s2 = _SAFE_NAME_RE.sub("_", s).strip("._-")
    return s2 or "cmd"


def shlex_split_posix(command_str: str) -> list[str]:
    # POSIX mode is default; explicitly set for clarity.
    return shlex.split(command_str, posix=True)


def cmd_to_string(argv: list[str]) -> str:
    # Deterministic, simple rendering (not shell-escaping).
    return " ".join(argv)


def run_command(
    *,
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    log_dir: Path,
    log_name: str,
) -> CmdResult:
    """
    Deterministic subprocess runner used everywhere.
    - never shell=True
    - captures stdout/stderr
    - enforces timeout
    - writes full stdout/stderr to files
    - returns CmdResult with truncated outputs
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    base = sanitize_filename_component(log_name)
    stdout_path = log_dir / f"{base}_stdout.txt"
    stderr_path = log_dir / f"{base}_stderr.txt"

    start = time.monotonic()
    try:
        cp = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = cp.stdout or ""
        stderr = cp.stderr or ""
        exit_code = int(cp.returncode)
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
        stderr = (e.stderr or "") if isinstance(e.stderr, str) else ""
        stderr = (stderr + "\n\n[TIMEOUT]\n").lstrip("\n")
        exit_code = 124
    duration = time.monotonic() - start

    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")

    return CmdResult(
        command=list(command),
        exit_code=exit_code,
        stdout_trunc=truncate_output(stdout),
        stderr_trunc=truncate_output(stderr),
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        duration_seconds=float(duration),
    )


def write_json(path: Path, payload: Any) -> None:
    """
    Pretty JSON, UTF-8, stable sort ordering.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(payload, "__class__") and payload.__class__.__name__.endswith("Model"):
        try:
            payload = model_dump(payload)  # type: ignore[assignment]
        except Exception:
            pass
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_json_model(path: Path, model: Any) -> None:
    write_pretty_json(path, model)


def bounded_read_text_files(
    repo_root: Path, rel_paths: list[str], *, max_total_bytes: int = 200_000
) -> list[tuple[str, str]]:
    """
    Deterministically read up to max_total_bytes across the given relative paths.
    Returns list of (rel_path, content) preserving input order.

    Truncation rule: read from the beginning of each file, up to remaining bytes.
    """
    out: list[tuple[str, str]] = []
    remaining = max_total_bytes
    for rel in rel_paths:
        p = (repo_root / rel).resolve()
        try:
            # Ensure the file is within repo_root
            if not is_path_within(p, repo_root):
                out.append((rel, "[ERROR] path resolves outside repo"))
                continue
        except Exception:
            pass
        if remaining <= 0:
            out.append((rel, "[TRUNCATED] byte budget exhausted"))
            continue
        try:
            b = p.read_bytes()
        except Exception as e:
            out.append((rel, f"[ERROR] failed to read: {e}"))
            continue
        if len(b) > remaining:
            b = b[:remaining]
            remaining = 0
            out.append((rel, b.decode("utf-8", errors="replace") + "\n[TRUNCATED]\n"))
        else:
            remaining -= len(b)
            out.append((rel, b.decode("utf-8", errors="replace")))
    return out


def ensure_outside_repo(repo_root: Path, out_dir: Path) -> None:
    if is_path_within(out_dir, repo_root):
        raise ValueError("out_dir must not be inside the repo path")


def stable_sorted_unique(items: Iterable[str]) -> list[str]:
    return sorted(set(items))

