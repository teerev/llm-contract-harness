import fnmatch
import json
import shlex
from pathlib import Path
import re

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)

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

    # If fenced, extract the fenced payload
    m = _JSON_FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()

    # If still not pure JSON, try to extract the first top-level JSON object
    # (very common failure: assistant prints commentary then an object)
    if not s.startswith("{"):
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1].strip()

    return json.loads(s)



def normalize_rel_path(p: str) -> str:
    return p.replace("\\", "/").lstrip("/")


def command_to_argv(cmd: str) -> list[str]:
    # for commands without shell pipelines/redirections.
    return shlex.split(cmd)
