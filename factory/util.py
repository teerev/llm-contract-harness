"""helper functions for paths, json, and other common operations."""

import fnmatch
import json
import shlex
from pathlib import Path



def safe_join(base: Path, rel: str) -> Path:
    """joins paths safely, blocking directory traversal attempts."""
    rel_path = Path(rel)
    if rel_path.is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {rel}")
    if ".." in rel_path.parts:
        raise ValueError(f"Path traversal is not allowed: {rel}")
    return (base / rel_path).resolve()


def matches_any_glob(path: str, globs: list[str]) -> bool:
    """returns true if the path matches any of the given globs."""
    norm = path.replace("\\", "/")
    return any(fnmatch.fnmatch(norm, g) for g in globs)


def strict_json_loads(s: str) -> dict:
    """parses a json string, stripping whitespace first."""
    s = s.strip()
    return json.loads(s)


def normalize_rel_path(p: str) -> str:
    """normalizes a relative path for consistent handling."""
    return p.replace("\\", "/").lstrip("/")


def command_to_argv(cmd: str) -> list[str]:
    """converts a shell command string into an argv list."""
    return shlex.split(cmd)
