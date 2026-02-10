"""Planner-level validation rules (on top of WorkOrder schema validation)."""

from __future__ import annotations

import ast
import posixpath
import re
import shlex
from dataclasses import dataclass, field as dc_field
from typing import Any

from factory.schemas import WorkOrder

VERIFY_COMMAND = "bash scripts/verify.sh"
VERIFY_SCRIPT_PATH = "scripts/verify.sh"
WO_ID_PATTERN = re.compile(r"^WO-\d{2}$")
GLOB_CHARS = set("*?[")
# Bare tokens that indicate shell operators — incompatible with shell=False.
# Checked against individual tokens after shlex.split, so semicolons and
# other characters *inside* quoted strings (e.g. python -c "a; b") are safe.
SHELL_OPERATOR_TOKENS = frozenset({"|", "||", "&&", ";", ">", ">>", "<", "<<"})


# ---------------------------------------------------------------------------
# Structured validation errors
# ---------------------------------------------------------------------------

# Error code constants — machine-readable, stable across versions.
#
# E0xx — per-work-order structural checks (validate_plan)
E000_STRUCTURAL = "E000"  # Top-level structure errors (empty list, bad JSON shape)
E001_ID = "E001"          # ID format / contiguity
E002_VERIFY = "E002"      # Verify command missing in acceptance
E003_SHELL_OP = "E003"    # Shell operator in acceptance command
E004_GLOB = "E004"        # Glob character in path
E005_SCHEMA = "E005"      # Pydantic schema validation failed
E006_SYNTAX = "E006"      # Python syntax error in python -c command
E007_SHLEX = "E007"       # Unparseable acceptance command (shlex.split failure)
#
# E1xx / W1xx — cross-work-order chain checks (validate_plan_v2)
E101_PRECOND = "E101"            # Precondition unsatisfied
E102_CONTRADICTION = "E102"      # Contradictory preconditions (same path, exists+absent)
E103_POST_OUTSIDE = "E103"       # Postcondition path not in allowed_files
E104_NO_POSTCOND = "E104"        # allowed_files entry has no postcondition
E105_VERIFY_IN_ACC = "E105"      # bash scripts/verify.sh in acceptance_commands
E106_VERIFY_CONTRACT = "E106"    # Verify contract never fully satisfied by plan
W101_ACCEPTANCE_DEP = "W101"     # Acceptance command depends on file not in cumulative state


@dataclass(frozen=True)
class ValidationError:
    """A structured validation error with a machine-readable code.

    Designed so that error codes can be fed back to the planner LLM in a
    revision prompt (M5 compile retry loop) for precise self-correction.
    """

    code: str
    wo_id: str | None
    message: str
    field: str | None = None

    def __str__(self) -> str:
        parts = [f"[{self.code}]"]
        if self.wo_id:
            parts.append(f"{self.wo_id}:")
        parts.append(self.message)
        return " ".join(parts)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON artifact output."""
        return {
            "code": self.code,
            "wo_id": self.wo_id,
            "message": self.message,
            "field": self.field,
        }


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _strip_strings(obj: Any) -> Any:
    """Recursively strip whitespace from all string values in a dict/list."""
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, list):
        return [_strip_strings(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _strip_strings(v) for k, v in obj.items()}
    return obj


def _deduplicate(items: list) -> list:
    """Deduplicate list preserving first-occurrence order."""
    seen: set = set()
    result: list = []
    for item in items:
        key = item if isinstance(item, str) else id(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def normalize_work_order(raw: dict) -> dict:
    """Strip whitespace, normpath all path fields, and deduplicate list fields.

    Returns a new dict.  M-06: applies ``posixpath.normpath`` to path-bearing
    fields so that ``"./src/a.py"`` and ``"src/a.py"`` are treated identically
    by the chain validator (matching the factory's schema-level normalization).
    """
    cleaned = _strip_strings(raw)

    # M-06: Normalize path strings so chain validation matches factory semantics.
    # Guard: only normpath non-empty strings (normpath("") == "." which would
    # hide empty-path errors from the schema validator).
    def _normpath_safe(p: str) -> str:
        return posixpath.normpath(p) if p else p

    for path_key in ("allowed_files", "context_files"):
        if path_key in cleaned and isinstance(cleaned[path_key], list):
            cleaned[path_key] = [
                _normpath_safe(p) if isinstance(p, str) else p
                for p in cleaned[path_key]
            ]
    for cond_key in ("preconditions", "postconditions"):
        if cond_key in cleaned and isinstance(cleaned[cond_key], list):
            for cond in cleaned[cond_key]:
                if isinstance(cond, dict) and isinstance(cond.get("path"), str):
                    cond["path"] = _normpath_safe(cond["path"])

    for list_key in ("allowed_files", "context_files", "forbidden", "acceptance_commands"):
        if list_key in cleaned and isinstance(cleaned[list_key], list):
            cleaned[list_key] = _deduplicate(cleaned[list_key])
    return cleaned


# ---------------------------------------------------------------------------
# Python -c syntax checking
# ---------------------------------------------------------------------------


def _check_python_c_syntax(
    cmd_str: str, wo_id: str
) -> ValidationError | None:
    """If *cmd_str* is a ``python -c "..."`` command, syntax-check the code.

    Returns a ``ValidationError`` with code ``E006`` on ``SyntaxError``,
    or ``None`` if the command is not a ``python -c`` invocation or the
    syntax is valid.
    """
    try:
        tokens = shlex.split(cmd_str)
    except ValueError as exc:
        # M-04: Emit E007 instead of silently skipping.
        return ValidationError(
            code=E007_SHLEX,
            wo_id=wo_id,
            message=(
                f"acceptance command has invalid shell syntax "
                f"(shlex.split failed: {exc}): {cmd_str!r}"
            ),
            field="acceptance_commands",
        )

    if len(tokens) >= 3 and tokens[0] == "python" and tokens[1] == "-c":
        code = tokens[2]
        try:
            ast.parse(code)
        except SyntaxError as exc:
            line_info = f" (line {exc.lineno})" if exc.lineno else ""
            return ValidationError(
                code=E006_SYNTAX,
                wo_id=wo_id,
                message=(
                    f"Python syntax error in acceptance command{line_info}: "
                    f"{exc.msg}: {cmd_str!r}"
                ),
                field="acceptance_commands",
            )
    return None


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------


def validate_plan(work_orders_raw: list[dict]) -> list[ValidationError]:
    """Validate a list of raw work-order dicts.

    Returns a list of ``ValidationError`` objects. An empty list means all
    validations passed.
    """
    errors: list[ValidationError] = []

    if not work_orders_raw:
        errors.append(ValidationError(
            code=E000_STRUCTURAL,
            wo_id=None,
            message="work_orders list is empty",
        ))
        return errors

    # --- M-03: Reject non-dict elements before normalization ---
    for i, wo in enumerate(work_orders_raw):
        if not isinstance(wo, dict):
            errors.append(ValidationError(
                code=E000_STRUCTURAL,
                wo_id=f"index-{i}",
                message=(
                    f"work_orders[{i}] is {type(wo).__name__}, expected dict"
                ),
            ))
    if errors:
        return errors

    # --- Normalize all work orders first ---
    normalized: list[dict] = [normalize_work_order(wo) for wo in work_orders_raw]

    # --- 1) ID format and contiguity ---
    for i, wo in enumerate(normalized):
        wo_id = wo.get("id", "")
        expected_id = f"WO-{i + 1:02d}"
        if not WO_ID_PATTERN.match(wo_id):
            errors.append(ValidationError(
                code=E001_ID,
                wo_id=wo_id or f"index-{i}",
                message=f"id {wo_id!r} does not match pattern WO-NN",
                field="id",
            ))
        elif wo_id != expected_id:
            errors.append(ValidationError(
                code=E001_ID,
                wo_id=wo_id,
                message=(
                    f"id {wo_id!r} should be {expected_id!r} "
                    f"(must be contiguous from WO-01)"
                ),
                field="id",
            ))

    # --- 2) Per-work-order checks ---
    for i, wo in enumerate(normalized):
        wo_id = wo.get("id", f"index-{i}")

        acceptance = wo.get("acceptance_commands", [])

        # NOTE: The old E002 rule ("acceptance_commands must contain
        # 'bash scripts/verify.sh'") and the WO-01 bootstrap exemption
        # have been removed.  The factory runs global verify automatically
        # via _get_verify_commands(); including it in acceptance was
        # redundant and created bootstrap circularity.  The inverse rule
        # (R7 — *ban* verify in acceptance) is enforced by validate_plan_v2.

        # Shell operators — commands run with shell=False, so pipes,
        # redirects, and chaining operators will not work.  We check the
        # shlex-split tokens (not the raw string) so that characters inside
        # quoted arguments (e.g. python -c "a; b") are safe.
        for cmd_str in acceptance:
            try:
                tokens = shlex.split(cmd_str)
            except ValueError as exc:
                # M-04: Emit E007 instead of silently skipping.
                errors.append(ValidationError(
                    code=E007_SHLEX,
                    wo_id=wo_id,
                    message=(
                        f"acceptance command has invalid shell syntax "
                        f"(shlex.split failed: {exc}): {cmd_str!r}"
                    ),
                    field="acceptance_commands",
                ))
                continue
            bad = [t for t in tokens if t in SHELL_OPERATOR_TOKENS]
            if bad:
                errors.append(ValidationError(
                    code=E003_SHELL_OP,
                    wo_id=wo_id,
                    message=(
                        f"acceptance command contains shell operator "
                        f"token(s) {bad} which are incompatible with shell=False "
                        f"execution: {cmd_str!r}"
                    ),
                    field="acceptance_commands",
                ))

        # Python -c syntax check
        for cmd_str in acceptance:
            err = _check_python_c_syntax(cmd_str, wo_id)
            if err is not None:
                errors.append(err)

        # Path hygiene — reject glob characters
        for field_name in ("allowed_files", "context_files"):
            for path in wo.get(field_name, []):
                if any(c in path for c in GLOB_CHARS):
                    errors.append(ValidationError(
                        code=E004_GLOB,
                        wo_id=wo_id,
                        message=f"{field_name} contains glob character in '{path}'",
                        field=field_name,
                    ))

        # Validate against WorkOrder schema
        try:
            WorkOrder(**wo)
        except Exception as exc:
            errors.append(ValidationError(
                code=E005_SCHEMA,
                wo_id=wo_id,
                message=f"schema validation failed: {exc}",
            ))

    return errors


def parse_and_validate(
    raw_json: dict,
) -> tuple[list[dict], list[ValidationError]]:
    """Parse the top-level LLM response, normalize, and validate.

    Returns ``(normalized_work_orders, errors)``.
    """
    if not isinstance(raw_json, dict):
        return [], [ValidationError(
            code=E000_STRUCTURAL,
            wo_id=None,
            message="Top-level JSON must be an object",
        )]

    work_orders_raw = raw_json.get("work_orders")
    if not isinstance(work_orders_raw, list):
        return [], [ValidationError(
            code=E000_STRUCTURAL,
            wo_id=None,
            message="Missing or invalid 'work_orders' key in response",
        )]

    # M-03: Reject non-dict elements before normalization to prevent crashes.
    non_dict_errors: list[ValidationError] = []
    for i, wo in enumerate(work_orders_raw):
        if not isinstance(wo, dict):
            non_dict_errors.append(ValidationError(
                code=E000_STRUCTURAL,
                wo_id=f"index-{i}",
                message=(
                    f"work_orders[{i}] is {type(wo).__name__}, expected dict"
                ),
            ))
    if non_dict_errors:
        return [], non_dict_errors

    # Normalize
    normalized = [normalize_work_order(wo) for wo in work_orders_raw]

    # Validate
    errors = validate_plan(work_orders_raw)

    return normalized, errors


# ---------------------------------------------------------------------------
# Acceptance-command dependency extraction
# ---------------------------------------------------------------------------

# Top-level stdlib module names; kept intentionally broad to avoid false
# positives.  Expand as needed — a missed stdlib name only causes a spurious
# W101 warning, never a hard error.
_STDLIB_TOP_LEVEL = frozenset({
    "abc", "ast", "asyncio", "base64", "builtins", "collections",
    "contextlib", "copy", "csv", "dataclasses", "datetime", "decimal",
    "enum", "functools", "glob", "hashlib", "importlib", "inspect",
    "io", "itertools", "json", "logging", "math", "multiprocessing",
    "operator", "os", "pathlib", "pickle", "pprint", "queue", "random",
    "re", "shlex", "shutil", "signal", "socket", "sqlite3", "string",
    "struct", "subprocess", "sys", "tempfile", "textwrap", "threading",
    "time", "traceback", "typing", "unittest", "urllib", "uuid",
    "warnings", "zipfile",
    # Common third-party that should never appear as project modules:
    "pytest", "pydantic", "numpy", "pip",
})


def _module_to_candidate_paths(module_name: str) -> list[str]:
    """Map a dotted module name to candidate file paths.

    ``mypackage.solver`` → ``["mypackage/solver.py", "mypackage/solver/__init__.py"]``
    """
    import posixpath

    parts = module_name.split(".")
    base = "/".join(parts)
    return [
        posixpath.normpath(f"{base}.py"),
        posixpath.normpath(f"{base}/__init__.py"),
    ]


def _extract_import_groups(cmd_str: str) -> list[tuple[str, list[str]]]:
    """Return ``[(module_name, [candidate_path, ...]), ...]`` for project imports.

    Only processes ``python -c "..."`` commands.  Returns an empty list for
    other command patterns or on parse failure.
    """
    try:
        tokens = shlex.split(cmd_str)
    except ValueError:
        return []

    if not (len(tokens) >= 3 and tokens[0] == "python" and tokens[1] == "-c"):
        return []

    code = tokens[2]
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # syntax errors caught by E006

    groups: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top not in _STDLIB_TOP_LEVEL:
                groups.append((node.module, _module_to_candidate_paths(node.module)))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in _STDLIB_TOP_LEVEL:
                    groups.append((alias.name, _module_to_candidate_paths(alias.name)))

    return groups


def extract_file_dependencies(cmd_str: str) -> list[str]:
    """Return a flat, deduplicated list of project file paths that *cmd_str* requires.

    Handles ``python -c "..."`` (via import analysis), ``bash path.sh``, and
    ``python path.py``.  Standard-library modules are excluded.
    """
    import posixpath

    try:
        tokens = shlex.split(cmd_str)
    except ValueError:
        return []

    deps: list[str] = []

    # Case 1: python -c "..."
    if len(tokens) >= 3 and tokens[0] == "python" and tokens[1] == "-c":
        for _module, candidates in _extract_import_groups(cmd_str):
            deps.extend(candidates)

    # Case 2: bash path/to/script.sh
    elif len(tokens) >= 2 and tokens[0] == "bash":
        deps.append(posixpath.normpath(tokens[1]))

    # Case 3: python path/to/script.py
    elif len(tokens) >= 2 and tokens[0] == "python" and tokens[1].endswith(".py"):
        deps.append(posixpath.normpath(tokens[1]))

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for d in deps:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


# ---------------------------------------------------------------------------
# Cross-work-order chain validator (validate_plan_v2)
# ---------------------------------------------------------------------------


def validate_plan_v2(
    work_orders: list[dict],
    verify_contract: dict | None,
    repo_file_listing: set[str],
) -> list[ValidationError]:
    """Validate the full work-order sequence for cross-WO chain consistency.

    This checks rules R1–R7 from the ACTION_PLAN.  It does **not** duplicate
    the per-WO structural checks in :func:`validate_plan` (E001–E006).

    Parameters
    ----------
    work_orders:
        Normalized work-order dicts (from :func:`parse_and_validate`).
    verify_contract:
        The ``verify_contract`` dict from the plan manifest, or ``None``
        for legacy plans without one.
    repo_file_listing:
        Set of relative file paths present in the target repo at baseline.
        Use ``set()`` for a fresh (empty) repo.

    Returns
    -------
    list[ValidationError]
        Empty list on success.
    """
    errors: list[ValidationError] = []

    # Cumulative file state: starts with the initial repo contents.
    file_state: set[str] = set(repo_file_listing)

    for wo in work_orders:
        wo_id: str = wo.get("id", "?")
        preconditions: list[dict] = wo.get("preconditions", [])
        postconditions: list[dict] = wo.get("postconditions", [])
        allowed_files: list[str] = wo.get("allowed_files", [])
        acceptance: list[str] = wo.get("acceptance_commands", [])

        # ── R7: Ban verify command in acceptance ─────────────────────
        # M-08: Normalize via shlex.split + posixpath.normpath so that
        # "bash  scripts/verify.sh", "bash ./scripts/verify.sh", etc.
        # are all caught — not just the exact string.
        for cmd_str in acceptance:
            try:
                tokens = shlex.split(cmd_str)
            except ValueError:
                continue  # shlex errors handled by E007 (M-04)
            normalized = tokens[:1] + [posixpath.normpath(t) for t in tokens[1:]]
            if normalized == ["bash", VERIFY_SCRIPT_PATH]:
                errors.append(ValidationError(
                    code=E105_VERIFY_IN_ACC,
                    wo_id=wo_id,
                    message=(
                        f"'{VERIFY_COMMAND}' (or equivalent) must not appear in "
                        f"acceptance_commands — the factory runs it "
                        f"automatically as a global gate: {cmd_str!r}"
                    ),
                    field="acceptance_commands",
                ))

        # ── R2: No contradictory preconditions ───────────────────────
        if preconditions:
            exists_paths = {
                c["path"] for c in preconditions if c.get("kind") == "file_exists"
            }
            absent_paths = {
                c["path"] for c in preconditions if c.get("kind") == "file_absent"
            }
            for p in sorted(exists_paths & absent_paths):
                errors.append(ValidationError(
                    code=E102_CONTRADICTION,
                    wo_id=wo_id,
                    message=(
                        f"contradictory preconditions: '{p}' is declared "
                        f"as both file_exists and file_absent"
                    ),
                    field="preconditions",
                ))

        # ── R1: Precondition satisfiability ──────────────────────────
        for cond in preconditions:
            kind = cond.get("kind")
            path = cond.get("path", "")
            if kind == "file_exists" and path not in file_state:
                errors.append(ValidationError(
                    code=E101_PRECOND,
                    wo_id=wo_id,
                    message=(
                        f"precondition file_exists('{path}') not satisfied "
                        f"— not in initial repo and no prior work order "
                        f"declares it as a postcondition"
                    ),
                    field="preconditions",
                ))
            elif kind == "file_absent" and path in file_state:
                errors.append(ValidationError(
                    code=E101_PRECOND,
                    wo_id=wo_id,
                    message=(
                        f"precondition file_absent('{path}') not satisfied "
                        f"— file already exists in cumulative state"
                    ),
                    field="preconditions",
                ))

        # ── R3: Postcondition achievability ──────────────────────────
        if postconditions:
            allowed_set = set(allowed_files)
            for cond in postconditions:
                path = cond.get("path", "")
                if path not in allowed_set:
                    errors.append(ValidationError(
                        code=E103_POST_OUTSIDE,
                        wo_id=wo_id,
                        message=(
                            f"postcondition file_exists('{path}') but "
                            f"path not in allowed_files"
                        ),
                        field="postconditions",
                    ))

        # ── R4: Allowed-files coverage ───────────────────────────────
        # Only enforced when the WO declares postconditions (backward compat).
        if postconditions:
            post_paths = {c.get("path") for c in postconditions}
            for path in allowed_files:
                if path not in post_paths:
                    errors.append(ValidationError(
                        code=E104_NO_POSTCOND,
                        wo_id=wo_id,
                        message=(
                            f"'{path}' is in allowed_files but has no "
                            f"file_exists postcondition"
                        ),
                        field="allowed_files",
                    ))

        # ── R5: Acceptance command dependencies (warnings) ───────────
        cumulative_after = file_state | {
            c["path"]
            for c in postconditions
            if c.get("kind") == "file_exists"
        }
        for cmd_str in acceptance:
            for module_name, candidates in _extract_import_groups(cmd_str):
                if not any(c in cumulative_after for c in candidates):
                    errors.append(ValidationError(
                        code=W101_ACCEPTANCE_DEP,
                        wo_id=wo_id,
                        message=(
                            f"acceptance command may depend on "
                            f"'{module_name}' (checked paths: "
                            f"{candidates}) which is not guaranteed "
                            f"to exist: {cmd_str!r}"
                        ),
                        field="acceptance_commands",
                    ))

        # ── Advance cumulative state ─────────────────────────────────
        for cond in postconditions:
            if cond.get("kind") == "file_exists":
                file_state.add(cond["path"])

    # ── R6: Verify contract reachability ─────────────────────────────
    # M-03: Guard against non-dict verify_contract.
    if verify_contract is not None and not isinstance(verify_contract, dict):
        errors.append(ValidationError(
            code=E000_STRUCTURAL,
            wo_id=None,
            message=(
                f"verify_contract is {type(verify_contract).__name__}, "
                f"expected dict or null"
            ),
            field="verify_contract",
        ))
        return errors
    if verify_contract is not None:
        vc_requires = verify_contract.get("requires", [])
        for req in vc_requires:
            kind = req.get("kind")
            path = req.get("path", "")
            if kind == "file_exists" and path not in file_state:
                errors.append(ValidationError(
                    code=E106_VERIFY_CONTRACT,
                    wo_id=None,
                    message=(
                        f"verify_contract is never fully satisfied by "
                        f"the plan — file_exists('{path}') not in "
                        f"cumulative state after last work order"
                    ),
                    field="verify_contract",
                ))

    return errors


def compute_verify_exempt(
    work_orders: list[dict],
    verify_contract: dict,
    repo_file_listing: set[str],
) -> list[dict]:
    """Compute ``verify_exempt`` for each work order and return updated dicts.

    A work order is verify-exempt if the cumulative file state *after* it
    does not satisfy every condition in ``verify_contract.requires``.

    Returns a **new** list of dicts (shallow copies with ``verify_exempt``
    injected).  The input list is not mutated.
    """
    # M-03: Guard against non-dict verify_contract (consistent with M-01).
    if not isinstance(verify_contract, dict):
        return [{**wo, "verify_exempt": False} for wo in work_orders]

    vc_requires = verify_contract.get("requires", [])
    if not vc_requires:
        # No contract → nothing is exempt; return copies with False.
        return [{**wo, "verify_exempt": False} for wo in work_orders]

    file_state: set[str] = set(repo_file_listing)
    result: list[dict] = []

    for wo in work_orders:
        postconditions: list[dict] = wo.get("postconditions", [])
        # Advance state with this WO's postconditions
        for cond in postconditions:
            if cond.get("kind") == "file_exists":
                file_state.add(cond["path"])

        # Check if every verify-contract requirement is satisfied
        satisfied = all(
            req.get("path", "") in file_state
            for req in vc_requires
            if req.get("kind") == "file_exists"
        )
        result.append({**wo, "verify_exempt": not satisfied})

    return result
