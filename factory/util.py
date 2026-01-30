import fnmatch
import json
import shlex
from pathlib import Path


def safe_join(base: Path, rel: str) -> Path:
    rel_path = Path(rel)
    if rel_path.is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {rel}")
    if ".." in rel_path.parts:
        raise ValueError(f"Path traversal is not allowed: {rel}")
    return (base / rel_path).resolve()


def matches_any_glob(path: str, globs: list[str]) -> bool:
    norm = path.replace("\\", "/")
    return any(fnmatch.fnmatch(norm, g) for g in globs)


def strict_json_loads(s: str) -> dict:
    s = s.strip()
    return json.loads(s)


def normalize_rel_path(p: str) -> str:
    return p.replace("\\", "/").lstrip("/")


def command_to_argv(cmd: str) -> list[str]:
    # for commands without shell pipelines/redirections.
    return shlex.split(cmd)
