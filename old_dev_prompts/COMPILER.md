# COMPILER.md — Precondition/Postcondition Validator Design

**Date:** 2026-02-08
**Branch:** `wo_compile`
**Status:** Design document. Not yet implemented.
**Prerequisite reading:** `planner/PLANNER_PROMPT.md`, `TO_FIX.md`

---

## 1. Problem Statement

The planner emits work orders validated only for structural correctness (schema,
ID contiguity, shell operators). There is no check that a work order is
**executable** given (a) the current state of the target repo and (b) the factory's
execution model.

The WO-01 bootstrap failure is the canonical example. The planner produced a
schema-valid WO-01 whose `acceptance_commands` included `bash scripts/verify.sh`.
That script runs `python -m pytest -q`, which exits 5 ("no tests collected")
because no test files exist yet. The factory faithfully executed the work order,
faithfully ran verify, and faithfully reported FAIL. The LLM executor had no way
to fix it—the failure was baked into the planner's contract.

**Root cause:** The planner has no language for declaring what must be true
*before* a work order runs or what will be true *after* it runs. Without this,
neither the planner validator nor the factory can reason about executability.
Failures that originate in the plan manifest as runtime FAILs or ERRORs,
wasting factory runs and LLM spend.

**Design goal:** Make it structurally impossible for the planner to emit a work
order whose preconditions are unsatisfied. If a work order fails at runtime
because a precondition was false, that is a **planner-contract bug** caught by
the validator—not something fixed by editing the target repo or individual work
orders.

---

## 2. Design Overview

```
                        PLANNER                        FACTORY
                    ┌──────────────┐              ┌──────────────┐
  spec.txt ───────►│  LLM pass    │              │              │
                    │  (generates  │   WO-NN.json │  SE → TR → PO│
  PLANNER_PROMPT ──►│  manifest)   ├─────────────►│  graph loop  │
                    └──────┬───────┘              └──────┬───────┘
                           │                             │
                    ┌──────▼───────┐              ┌──────▼───────┐
                    │  VALIDATOR   │              │  RUNTIME     │
                    │  (this doc)  │              │  CHECKS      │
                    │              │              │  (this doc)  │
                    │  structural  │              │  pre: check  │
                    │  chain check │              │    preconds  │
                    │  dependency  │              │  post: check │
                    │  resolution  │              │    postconds │
                    └──────────────┘              └──────────────┘
```

**Three layers of defense:**

1. **Plan-time validator** (deterministic, no execution, in `planner/validation.py`):
   Checks the entire work-order sequence for structural consistency. Rejects
   plans where any WO's preconditions cannot be satisfied by the initial repo
   state plus the cumulative postconditions of prior WOs.

2. **Factory precondition gate** (deterministic, in factory before SE runs):
   Checks that every declared precondition holds in the actual repo right now.
   If a precondition fails, the factory reports a clear error:
   `PLANNER-CONTRACT BUG: precondition X not satisfied`.

3. **Factory postcondition gate** (deterministic, in factory after TR writes):
   Checks that every declared postcondition holds after writes are applied.
   If a postcondition fails, the factory reports the failure before running
   acceptance commands, giving a precise diagnostic.

---

## 3. Schema Changes

### 3.1 Condition Model

New model in `factory/schemas.py`:

```python
from typing import Literal

class Condition(BaseModel):
    """A deterministically checkable assertion about the repo state."""
    kind: Literal["file_exists", "file_absent"]
    path: str  # relative path, validated like allowed_files

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        return _validate_relative_path(v)
```

**`file_exists`**: The file at `path` exists in the repo working tree.
Checkable at plan time (against cumulative state) and at runtime (`os.path.isfile`).

**`file_absent`**: The file at `path` does NOT exist. Useful as a precondition
for "create new file" work orders—ensures no accidental overwrite of a file the
planner didn't account for.

Postconditions are restricted to `file_exists` only. The factory cannot delete
files (it only writes), so `file_absent` as a postcondition is nonsensical.

### 3.2 WorkOrder Extension

Two new fields on `WorkOrder` in `factory/schemas.py`:

```python
class WorkOrder(BaseModel):
    id: str
    title: str
    intent: str
    preconditions: list[Condition] = []     # NEW
    postconditions: list[Condition] = []    # NEW
    allowed_files: list[str]
    forbidden: list[str]
    acceptance_commands: list[str]
    context_files: list[str]
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_postconditions_file_exists_only(self) -> "WorkOrder":
        for c in self.postconditions:
            if c.kind != "file_exists":
                raise ValueError(
                    f"postconditions may only use 'file_exists', got '{c.kind}' "
                    f"for path '{c.path}'"
                )
        return self
```

Backward compatibility: both new fields default to `[]`, so existing WO JSON
files parse without error. The validator treats empty conditions as "no
assertions" and applies only the pre-existing structural checks.

### 3.3 Plan Manifest Extension

The top-level manifest gains a `verify_contract` field:

```json
{
  "system_overview": ["..."],
  "verify_contract": {
    "command": "python -m pytest -q",
    "requires": [
      {"kind": "file_exists", "path": "scripts/verify.sh"},
      {"kind": "file_exists", "path": "tests/test_placeholder.py"}
    ]
  },
  "work_orders": [...]
}
```

**`verify_contract.command`**: The shell command that `scripts/verify.sh`
will execute. Informational—used by the validator to apply command-specific
heuristic rules (e.g., pytest requires test files).

**`verify_contract.requires`**: List of `Condition` objects that must be true
for the global verify command to succeed. The validator uses this to compute
which work orders are **verify-exempt** (see section 4.4).

### 3.4 Computed Field: `verify_exempt`

Added to WorkOrder with a default of `False`:

```python
class WorkOrder(BaseModel):
    # ... existing + new fields ...
    verify_exempt: bool = False  # computed by validator, not emitted by planner
```

The planner does NOT emit this field. The validator computes it from the
`verify_contract` and cumulative postconditions, then injects it before writing
the WO JSON files. The factory's PO node reads it to decide whether to run
global verify.

---

## 4. Plan-Time Validator

### 4.1 Inputs

```python
def validate_plan_v2(
    work_orders: list[dict],
    verify_contract: dict | None,
    repo_file_listing: set[str],   # files in initial repo (relative paths)
) -> list[str]:
    """Validate the full work-order sequence. Returns error strings."""
```

- **`work_orders`**: Normalized WO dicts from the planner.
- **`verify_contract`**: From the manifest. `None` if omitted (legacy plans).
- **`repo_file_listing`**: Set of relative file paths in the target repo at
  baseline commit. Obtained by `git ls-files` or `os.walk`. Passed in by the
  caller (planner CLI or test harness).

### 4.2 Cumulative State Tracking

The validator maintains a **cumulative file state** as it processes each WO:

```python
# Initialize with actual repo contents
file_state: set[str] = set(repo_file_listing)

for wo in work_orders:
    # Check preconditions against current file_state
    ...
    # After validation, apply postconditions
    for post in wo["postconditions"]:
        if post["kind"] == "file_exists":
            file_state.add(post["path"])
    # (file_absent postconditions are forbidden by schema, so no removals)
```

This lets the validator trace file existence across the entire sequence without
executing anything.

### 4.3 Validation Rules

All existing rules in `validation.py` are preserved (ID contiguity, shell
operators, glob characters, schema conformance). The following rules are **new**.

---

#### R1: Precondition Satisfiability

> For each WO-N, every precondition must be satisfied by the cumulative file
> state (initial repo + postconditions of WO-1 through WO-(N-1)).

```
For condition {kind: "file_exists", path: P}:
    P must be in file_state.

For condition {kind: "file_absent", path: P}:
    P must NOT be in file_state.
```

**Catches:** Missing dependency. WO-03 declares
`precondition: file_exists("src/models.py")` but no prior WO creates it and
it's not in the initial repo.

---

#### R2: No Contradictory Preconditions

> No work order may declare both `file_exists(P)` and `file_absent(P)` for
> the same path.

```
exists_paths = {c.path for c in wo.preconditions if c.kind == "file_exists"}
absent_paths = {c.path for c in wo.preconditions if c.kind == "file_absent"}
contradictions = exists_paths & absent_paths
if contradictions:
    ERROR
```

**Catches:** Self-contradictory work orders—the planner asserting a file both
exists and doesn't exist.

---

#### R3: Postcondition Achievability

> Every `file_exists` postcondition must reference a path that is in the work
> order's `allowed_files`. If you declare you'll create a file, you must be
> allowed to write it.

```
allowed_set = set(wo.allowed_files)
for post in wo.postconditions:
    if post.path not in allowed_set:
        ERROR: f"postcondition file_exists('{post.path}') but path not in allowed_files"
```

**Catches:** Planner declaring outcomes it can't achieve.

---

#### R4: Allowed-Files Coverage

> Every path in `allowed_files` must appear as a `file_exists` postcondition.
> If you're allowed to write a file, you must declare that you wrote it, so
> downstream WOs can depend on it.

```
postcond_paths = {c.path for c in wo.postconditions}
for path in wo.allowed_files:
    if path not in postcond_paths:
        WARNING (or ERROR): f"'{path}' is in allowed_files but has no postcondition"
```

This may be a warning rather than a hard error if there are legitimate cases
where an allowed file is conditionally written. Start as an error; downgrade
if false positives emerge.

**Catches:** Missing file-path declaration. The planner lists a file in
`allowed_files` but forgets to declare it as a postcondition, making it
invisible to downstream dependency resolution.

---

#### R5: Acceptance Command Dependencies

> For each acceptance command, derive the set of project files it depends on.
> Each dependency must be in the cumulative state (initial repo + postconditions
> of WO-1 through WO-N, inclusive of the current WO's postconditions).

Dependency extraction for common command patterns:

| Pattern | Extraction |
|---------|------------|
| `python -c "from X.Y import Z"` | Parse with `ast.parse`, walk `ImportFrom`/`Import` nodes, map `X.Y` → `X/Y.py` and `X/Y/__init__.py` |
| `python -c "import X"` | → `X.py` or `X/__init__.py` |
| `bash path/to/script.sh` | → `path/to/script.sh` |
| `python path/to/script.py` | → `path/to/script.py` |

Standard library modules (`os`, `sys`, `json`, `re`, `pathlib`, etc.) are
excluded via a hardcoded allowlist.

```
deps = extract_file_dependencies(cmd_str)
cumulative_state = file_state | {c.path for c in wo.postconditions if c.kind == "file_exists"}
for dep in deps:
    if dep not in cumulative_state:
        ERROR: f"acceptance command depends on '{dep}' which is not guaranteed to exist"
```

**Catches:** Unverifiable acceptance. WO-05 has
`python -c "from mypackage.solver import Solver"` but no WO creates
`mypackage/solver.py`.

---

#### R6: Verify-Exempt Computation

> For each WO-N, check whether the cumulative state after WO-N satisfies all
> conditions in `verify_contract.requires`. If not, mark `verify_exempt: true`.

```
vc_requires = set of conditions from verify_contract.requires

for each WO-N:
    cumulative_after_N = initial_repo + postconditions(WO-1..WO-N)
    if all conditions in vc_requires are satisfied by cumulative_after_N:
        wo["verify_exempt"] = False
    else:
        wo["verify_exempt"] = True
```

Additionally: the verify_contract must be fully satisfied by the cumulative
state after the **last** work order. If it's not, the plan is invalid—verify
will never pass:

```
if not all_satisfied(vc_requires, final_cumulative_state):
    ERROR: "verify_contract is never fully satisfied by the plan"
```

**Catches:** Bootstrap verify circularity. WO-01 creates `scripts/verify.sh`
but no test files. The verify_contract requires both. So WO-01 is
`verify_exempt: true`. The factory skips global verify for WO-01.

---

#### R7: Verify Command Not in Acceptance

> `bash scripts/verify.sh` must NOT appear in any work order's
> `acceptance_commands`. Global verification is the factory's responsibility
> (via `_get_verify_commands` in `nodes_po.py`). Including it in acceptance
> is redundant and creates bootstrap circularity risk.

```
for cmd in wo.acceptance_commands:
    if cmd.strip() == "bash scripts/verify.sh":
        ERROR: "verify command must not appear in acceptance_commands; "
               "it is run automatically by the factory as a global gate"
```

This replaces the current rule that *requires* `bash scripts/verify.sh` in
acceptance and its WO-01 exemption. The new rule is simpler: never include it.

---

### 4.4 Acceptance Command Import Extraction

Implementation sketch for `extract_file_dependencies`:

```python
import ast
import shlex

# Top ~50 stdlib module names; expand as needed.
_STDLIB = frozenset({
    "abc", "ast", "asyncio", "base64", "builtins", "collections",
    "contextlib", "copy", "csv", "dataclasses", "datetime", "decimal",
    "enum", "functools", "glob", "hashlib", "importlib", "inspect",
    "io", "itertools", "json", "logging", "math", "operator", "os",
    "pathlib", "pickle", "pprint", "random", "re", "shlex", "shutil",
    "signal", "socket", "sqlite3", "string", "struct", "subprocess",
    "sys", "tempfile", "textwrap", "threading", "time", "traceback",
    "typing", "unittest", "urllib", "uuid", "warnings",
})


def _module_to_paths(module_name: str) -> list[str]:
    """Map a dotted module name to candidate file paths."""
    parts = module_name.split(".")
    base = "/".join(parts)
    return [f"{base}.py", f"{base}/__init__.py"]


def extract_file_dependencies(cmd_str: str) -> list[str]:
    """Return project-file paths that *cmd_str* implicitly requires."""
    tokens = shlex.split(cmd_str)
    deps: list[str] = []

    # Case 1: python -c "..."
    if len(tokens) >= 3 and tokens[0] == "python" and tokens[1] == "-c":
        code = tokens[2]
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []  # syntax errors caught by existing ast.parse check
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top not in _STDLIB:
                    deps.extend(_module_to_paths(node.module))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in _STDLIB:
                        deps.extend(_module_to_paths(alias.name))

    # Case 2: bash path/to/script.sh
    elif len(tokens) >= 2 and tokens[0] == "bash":
        deps.append(tokens[1])

    # Case 3: python path/to/script.py
    elif len(tokens) >= 2 and tokens[0] == "python" and tokens[1].endswith(".py"):
        deps.append(tokens[1])

    return deps
```

This is intentionally conservative—it catches common patterns and ignores
dynamic imports. False negatives are acceptable (the factory's runtime checks
catch them). False positives are unacceptable (would reject valid plans), so
only well-understood patterns are handled.

### 4.5 Integration with `compile_plan()`

In `planner/compiler.py`, after `parse_and_validate` returns the existing
structural checks:

```python
# --- Existing structural validation ---
work_orders, errors = parse_and_validate(parsed)

# --- NEW: chain-consistency validation ---
if not errors:
    verify_contract = parsed.get("verify_contract")
    # repo_file_listing is passed in by the CLI (or empty set for new repos)
    chain_errors = validate_plan_v2(
        work_orders,
        verify_contract=verify_contract,
        repo_file_listing=repo_file_listing,
    )
    errors.extend(chain_errors)

# --- NEW: compute verify_exempt ---
if not errors and verify_contract:
    work_orders = compute_verify_exempt(work_orders, verify_contract, repo_file_listing)
```

The CLI (`planner/cli.py`) gains a `--repo` flag that points to the target
repo. The compile command reads the file listing from it:

```python
repo_file_listing = set()
if args.repo:
    for root, dirs, files in os.walk(args.repo):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), args.repo)
            repo_file_listing.add(rel)
```

If `--repo` is not provided, `repo_file_listing` defaults to the empty set
(fresh repo assumed).

---

## 5. Factory Runtime Checks

### 5.1 Precondition Gate

A new check in the factory, executed **before** the SE node runs. This can be
a new node (`precond_node`) or a check at the start of `se_node`.

Recommended: add it to `se_node` at the top, before reading context files:

```python
# In se_node(), before _read_context_files:
work_order = WorkOrder(**state["work_order"])

for cond in work_order.preconditions:
    abs_path = os.path.join(repo_root, cond.path)
    if cond.kind == "file_exists" and not os.path.isfile(abs_path):
        fb = FailureBrief(
            stage="preflight",
            primary_error_excerpt=(
                f"PLANNER-CONTRACT BUG: precondition file_exists('{cond.path}') "
                f"is false. The file does not exist."
            ),
            constraints_reminder=(
                "This is a plan-level error. The work order sequence is invalid. "
                "Re-run the planner."
            ),
        )
        return {"proposal": None, "write_ok": False, "failure_brief": fb.model_dump()}
    elif cond.kind == "file_absent" and os.path.isfile(abs_path):
        fb = FailureBrief(
            stage="preflight",
            primary_error_excerpt=(
                f"PLANNER-CONTRACT BUG: precondition file_absent('{cond.path}') "
                f"is false. The file already exists."
            ),
            constraints_reminder=(
                "This is a plan-level error. The work order sequence is invalid. "
                "Re-run the planner."
            ),
        )
        return {"proposal": None, "write_ok": False, "failure_brief": fb.model_dump()}
```

If a precondition fails at runtime, the error message explicitly says
**PLANNER-CONTRACT BUG**. This distinguishes plan-level errors (which the
executor LLM cannot fix) from execution-level errors (which retry might fix).

### 5.2 Postcondition Gate

A new check in `po_node`, after TR writes are applied and before acceptance
commands run:

```python
# In po_node(), after verify commands, before acceptance:
for cond in work_order.postconditions:
    abs_path = os.path.join(repo_root, cond.path)
    if cond.kind == "file_exists" and not os.path.isfile(abs_path):
        fb = FailureBrief(
            stage="acceptance_failed",
            primary_error_excerpt=(
                f"Postcondition file_exists('{cond.path}') is false after writes. "
                f"The executor did not create the expected file."
            ),
            constraints_reminder=(
                "The executor must create all files declared in postconditions."
            ),
        )
        # ... return failure ...
```

Unlike precondition failures, postcondition failures are **executor errors**
(the LLM didn't write the right files). These are retryable.

### 5.3 verify_exempt Handling in PO Node

In `po_node`, replace the unconditional verify with:

```python
# In po_node(), global verification section:
if not work_order.verify_exempt:
    verify_cmds = _get_verify_commands(repo_root)
    # ... run verify commands as before ...
else:
    # Skip global verify; use lightweight fallback only
    verify_cmds = [["python", "-m", "compileall", "-q", "."]]
    # ... run fallback ...
```

When `verify_exempt` is `True`, the factory runs only `compileall` (syntax
check), not the full verify script. This ensures basic sanity without requiring
the full verify contract to be satisfied.

---

## 6. Planner Prompt Changes

Changes to `planner/PLANNER_PROMPT.md`:

### 6.1 New Fields in Work Order Schema

Add to the "WORK ORDER DESIGN RULES" section:

```
- `preconditions`: list of objects, each with:
    - `kind`: "file_exists" or "file_absent"
    - `path`: relative file path
  Declares what must be true BEFORE this work order executes.
  Use `file_exists` to declare dependencies on files created by prior WOs.
  Use `file_absent` to assert a file does not yet exist (create-only safety).

- `postconditions`: list of objects, each with:
    - `kind`: "file_exists" (only allowed kind for postconditions)
    - `path`: relative file path
  Declares what will be true AFTER this work order executes.
  Every file in `allowed_files` MUST appear as a postcondition.
```

### 6.2 Remove Verify from Acceptance Commands

Replace the current rule:

> `acceptance_commands`: list of strings, must include `bash scripts/verify.sh`

With:

> `acceptance_commands`: list of strings. Each command independently verifies
> the work order's specific intent. Do NOT include `bash scripts/verify.sh` —
> global verification is run automatically by the factory.

### 6.3 Verify Contract in Manifest

Add to the "OUTPUT FORMAT" section:

```json
{
  "system_overview": ["..."],
  "verify_contract": {
    "command": "python -m pytest -q",
    "requires": [
      {"kind": "file_exists", "path": "scripts/verify.sh"},
      {"kind": "file_exists", "path": "tests/test_placeholder.py"}
    ]
  },
  "work_orders": [...]
}
```

> `verify_contract`: Declares the conditions required for `scripts/verify.sh`
> to succeed. The `requires` list must include `scripts/verify.sh` itself plus
> any files the verify command depends on (e.g., test files for pytest).
> The validator uses this to determine which early work orders are exempt
> from global verification.

### 6.4 Updated WO-01 Bootstrapping Contract

Replace the existing WO-01 section with:

> WO-01 creates `scripts/verify.sh`. Its preconditions should be empty (or
> `file_absent` for verify.sh if the repo doesn't have one). Its postconditions
> must include `file_exists("scripts/verify.sh")`. WO-01 is automatically
> `verify_exempt` (the validator computes this from the verify_contract).
>
> WO-01 MUST NOT include `bash scripts/verify.sh` in acceptance_commands.
> Use independent checks only (e.g., file-existence assertion).

### 6.5 Example WO-01 with Conditions

```json
{
  "id": "WO-01",
  "title": "Bootstrap verify script",
  "intent": "Create scripts/verify.sh as the global verification command.",
  "preconditions": [
    {"kind": "file_absent", "path": "scripts/verify.sh"}
  ],
  "postconditions": [
    {"kind": "file_exists", "path": "scripts/verify.sh"}
  ],
  "allowed_files": ["scripts/verify.sh"],
  "forbidden": ["Do not create any other files."],
  "acceptance_commands": [
    "python -c \"import os; assert os.path.isfile('scripts/verify.sh')\""
  ],
  "context_files": ["scripts/verify.sh"],
  "notes": "scripts/verify.sh must contain exactly: #!/usr/bin/env bash\\nset -euo pipefail\\npython -m pytest -q"
}
```

### 6.6 Example WO-02 with Conditions

```json
{
  "id": "WO-02",
  "title": "Project skeleton and placeholder test",
  "intent": "Create package init and a passing placeholder test.",
  "preconditions": [
    {"kind": "file_exists", "path": "scripts/verify.sh"},
    {"kind": "file_absent", "path": "mypackage/__init__.py"},
    {"kind": "file_absent", "path": "tests/test_placeholder.py"}
  ],
  "postconditions": [
    {"kind": "file_exists", "path": "mypackage/__init__.py"},
    {"kind": "file_exists", "path": "tests/test_placeholder.py"}
  ],
  "allowed_files": ["mypackage/__init__.py", "tests/test_placeholder.py"],
  "forbidden": ["Do not modify scripts/verify.sh."],
  "acceptance_commands": [
    "python -c \"import mypackage\""
  ],
  "context_files": [
    "scripts/verify.sh",
    "mypackage/__init__.py",
    "tests/test_placeholder.py"
  ],
  "notes": "..."
}
```

After WO-02, the cumulative state includes `scripts/verify.sh` AND
`tests/test_placeholder.py`, which satisfies the verify_contract. So WO-02
is NOT verify-exempt, and the factory runs `bash scripts/verify.sh` after
WO-02 completes. Pytest finds `test_placeholder.py` and passes.

---

## 7. Regression Tests

All tests go in a new file: `tests/test_validator.py`.

### 7.1 Test Infrastructure

```python
"""Tests for the plan-time precondition/postcondition validator."""

import pytest
from planner.validation import validate_plan_v2


def _wo(id, **overrides):
    """Build a minimal valid work order dict with conditions."""
    base = {
        "id": id,
        "title": f"Test {id}",
        "intent": "test intent",
        "preconditions": [],
        "postconditions": [
            {"kind": "file_exists", "path": p}
            for p in overrides.get("allowed_files", ["src/a.py"])
        ],
        "allowed_files": ["src/a.py"],
        "forbidden": [],
        "acceptance_commands": [
            'python -c "assert True"'
        ],
        "context_files": ["src/a.py"],
        "notes": None,
    }
    base.update(overrides)
    return base


EMPTY_REPO = set()          # no files in initial repo
VERIFY_CONTRACT = {
    "command": "python -m pytest -q",
    "requires": [
        {"kind": "file_exists", "path": "scripts/verify.sh"},
        {"kind": "file_exists", "path": "tests/test_placeholder.py"},
    ],
}
```

### 7.2 Case 1: Bootstrap Verify Circularity

```python
def test_bootstrap_verify_in_acceptance_rejected():
    """WO-01 creates verify.sh and uses it as acceptance → rejected."""
    wo = _wo(
        "WO-01",
        allowed_files=["scripts/verify.sh"],
        postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}],
        acceptance_commands=["bash scripts/verify.sh"],
        context_files=["scripts/verify.sh"],
    )
    errors = validate_plan_v2([wo], VERIFY_CONTRACT, EMPTY_REPO)
    assert any("verify command must not appear in acceptance" in e for e in errors)
```

This tests rule R7: `bash scripts/verify.sh` is never allowed in acceptance.

### 7.3 Case 2: Missing Dependency

```python
def test_missing_dependency_rejected():
    """WO-02 requires a file that no prior WO creates → rejected."""
    wo1 = _wo(
        "WO-01",
        allowed_files=["scripts/verify.sh"],
        postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}],
        preconditions=[],
        acceptance_commands=['python -c "assert True"'],
        context_files=["scripts/verify.sh"],
    )
    wo2 = _wo(
        "WO-02",
        allowed_files=["src/app.py"],
        preconditions=[
            {"kind": "file_exists", "path": "src/models.py"},  # never created!
        ],
        postconditions=[{"kind": "file_exists", "path": "src/app.py"}],
        acceptance_commands=['python -c "assert True"'],
        context_files=["src/app.py", "src/models.py"],
    )
    errors = validate_plan_v2([wo1, wo2], VERIFY_CONTRACT, EMPTY_REPO)
    assert any("src/models.py" in e and "not satisfied" in e for e in errors)
```

This tests rule R1: precondition `file_exists("src/models.py")` is not in the
cumulative state when WO-02 runs.

### 7.4 Case 3: Contradictory Preconditions

```python
def test_contradictory_preconditions_rejected():
    """WO declares file_exists AND file_absent for same path → rejected."""
    wo = _wo(
        "WO-01",
        allowed_files=["src/a.py"],
        preconditions=[
            {"kind": "file_exists", "path": "src/a.py"},
            {"kind": "file_absent", "path": "src/a.py"},
        ],
        postconditions=[{"kind": "file_exists", "path": "src/a.py"}],
    )
    errors = validate_plan_v2([wo], None, EMPTY_REPO)
    assert any("contradictory" in e.lower() for e in errors)
```

This tests rule R2.

### 7.5 Case 4: Missing File Path (Postcondition Outside allowed_files)

```python
def test_postcondition_outside_allowed_files_rejected():
    """Postcondition references a file not in allowed_files → rejected."""
    wo = _wo(
        "WO-01",
        allowed_files=["src/a.py"],
        postconditions=[
            {"kind": "file_exists", "path": "src/a.py"},
            {"kind": "file_exists", "path": "src/b.py"},   # not allowed!
        ],
    )
    errors = validate_plan_v2([wo], None, EMPTY_REPO)
    assert any("src/b.py" in e and "not in allowed_files" in e for e in errors)
```

This tests rule R3.

### 7.6 Case 5: Unverifiable Acceptance

```python
def test_unverifiable_acceptance_rejected():
    """Acceptance imports a module whose file is never created → rejected."""
    wo1 = _wo(
        "WO-01",
        allowed_files=["scripts/verify.sh"],
        postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}],
        acceptance_commands=['python -c "assert True"'],
        context_files=["scripts/verify.sh"],
    )
    wo2 = _wo(
        "WO-02",
        allowed_files=["mypackage/__init__.py"],
        preconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}],
        postconditions=[{"kind": "file_exists", "path": "mypackage/__init__.py"}],
        acceptance_commands=[
            'python -c "from mypackage.solver import Solver"'
            # mypackage/solver.py is never created by any WO!
        ],
        context_files=["mypackage/__init__.py"],
    )
    errors = validate_plan_v2([wo1, wo2], VERIFY_CONTRACT, EMPTY_REPO)
    assert any("mypackage/solver" in e for e in errors)
```

This tests rule R5: the acceptance command imports `mypackage.solver`, but no
work order declares `file_exists("mypackage/solver.py")` as a postcondition.

### 7.7 Additional Cases

Beyond the required 5, these are recommended:

```python
def test_verify_contract_never_satisfied_rejected():
    """Plan where verify_contract is never fully satisfied → rejected."""
    # Only WO-01 (creates verify.sh) but no WO creates test files.
    wo = _wo(
        "WO-01",
        allowed_files=["scripts/verify.sh"],
        postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}],
        acceptance_commands=['python -c "assert True"'],
        context_files=["scripts/verify.sh"],
    )
    errors = validate_plan_v2([wo], VERIFY_CONTRACT, EMPTY_REPO)
    assert any("verify_contract is never fully satisfied" in e for e in errors)


def test_allowed_file_without_postcondition_rejected():
    """File in allowed_files but missing from postconditions → rejected."""
    wo = _wo(
        "WO-01",
        allowed_files=["src/a.py", "src/b.py"],
        postconditions=[{"kind": "file_exists", "path": "src/a.py"}],
        # src/b.py has no postcondition!
    )
    errors = validate_plan_v2([wo], None, EMPTY_REPO)
    assert any("src/b.py" in e for e in errors)


def test_valid_two_wo_plan_passes():
    """Well-formed two-WO plan with proper conditions → no errors."""
    wo1 = _wo(
        "WO-01",
        allowed_files=["scripts/verify.sh"],
        preconditions=[{"kind": "file_absent", "path": "scripts/verify.sh"}],
        postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}],
        acceptance_commands=[
            'python -c "import os; assert os.path.isfile(\'scripts/verify.sh\')"'
        ],
        context_files=["scripts/verify.sh"],
    )
    wo2 = _wo(
        "WO-02",
        allowed_files=["mypackage/__init__.py", "tests/test_placeholder.py"],
        preconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}],
        postconditions=[
            {"kind": "file_exists", "path": "mypackage/__init__.py"},
            {"kind": "file_exists", "path": "tests/test_placeholder.py"},
        ],
        acceptance_commands=['python -c "import mypackage"'],
        context_files=[
            "scripts/verify.sh",
            "mypackage/__init__.py",
            "tests/test_placeholder.py",
        ],
    )
    errors = validate_plan_v2([wo1, wo2], VERIFY_CONTRACT, EMPTY_REPO)
    assert errors == []


def test_verify_exempt_computed_correctly():
    """WO-01 should be verify_exempt, WO-02 should not."""
    wo1 = _wo(...)  # creates verify.sh only
    wo2 = _wo(...)  # creates test file
    # After compute_verify_exempt:
    assert wo1["verify_exempt"] is True
    assert wo2["verify_exempt"] is False
```

---

## 8. Implementation Phases

### Phase 1: Schema + Validator Core (no factory changes, no prompt changes)

**Files changed:** `factory/schemas.py`, `planner/validation.py`,
`tests/test_validator.py`

1. Add `Condition` model to `factory/schemas.py`.
2. Add `preconditions`, `postconditions`, `verify_exempt` fields to `WorkOrder`
   (all optional with defaults for backward compatibility).
3. Implement `validate_plan_v2()` in `planner/validation.py` with rules R1–R7.
4. Implement `extract_file_dependencies()` in `planner/validation.py`.
5. Implement `compute_verify_exempt()` in `planner/validation.py`.
6. Write all regression tests in `tests/test_validator.py`.
7. Run existing test suite—all 123 tests must still pass.

**Why first:** The validator is pure logic with no external dependencies. It
can be developed and tested in isolation. The existing system continues to work
unchanged because the new fields default to empty.

### Phase 2: Planner Prompt + Compile Integration

**Files changed:** `planner/PLANNER_PROMPT.md`, `planner/compiler.py`,
`planner/cli.py`, `planner/validation.py` (wire new checks into
`parse_and_validate`)

1. Update `PLANNER_PROMPT.md` per section 6.
2. Add `--repo` flag to `planner/cli.py`.
3. Wire `validate_plan_v2` into `compile_plan()` per section 4.5.
4. Wire `compute_verify_exempt` into `compile_plan()`.
5. Test: run the planner against `spec.txt` with the new prompt and verify
   the emitted WOs have valid conditions.

**Why second:** Prompt changes are the riskiest part (LLM behavior is
stochastic). Having the validator ready means you can immediately test whether
the LLM produces valid plans.

### Phase 3: Factory Runtime Checks

**Files changed:** `factory/nodes_se.py`, `factory/nodes_po.py`,
`factory/schemas.py` (if `verify_exempt` needs additional validation)

1. Add precondition gate to `se_node` per section 5.1.
2. Add postcondition gate to `po_node` per section 5.2.
3. Add `verify_exempt` handling to `po_node` per section 5.3.
4. Update `tests/test_nodes.py` with unit tests for precondition/postcondition
   checking.

**Why third:** Factory changes are low-risk (they're additive checks) but
depend on the schema changes from Phase 1. Running them last means the planner
is already producing valid conditions.

### Phase 4: Compile Retry Loop (from W1 in TO_FIX.md)

**Files changed:** `planner/compiler.py`, `planner/validation.py`

This phase implements the deterministic feedback loop described in TO_FIX.md
workplan W1, now enhanced with the new validator rules:

1. Add `ast.parse` check for `python -c` commands.
2. Add `_build_revision_prompt()` for feeding validation errors back to the LLM.
3. Add retry loop to `compile_plan()`.
4. The new R1–R7 rules are automatically included in the retry loop because
   they're wired into the same validation pipeline.

---

## 9. Migration and Backward Compatibility

### Existing work orders (no conditions)

The new fields default to `[]` and `False`. Existing WO JSON files parse
without error. The validator skips condition-based rules when conditions are
empty, applying only the pre-existing structural checks.

### Existing prompt (still requires `bash scripts/verify.sh`)

The prompt change (removing verify from acceptance) is a breaking change for
the planner output format. It should be deployed atomically with the
validation rule change (R7). During development:

1. Add R7 (reject verify in acceptance) behind a feature flag.
2. Update the prompt.
3. Re-run the planner; verify the new output passes validation.
4. Remove the feature flag.

### Existing factory (no condition checks)

The factory changes are purely additive. When `preconditions` and
`postconditions` are empty (old-format WOs), the gates are no-ops.

### Existing validation (requires verify in acceptance)

The current rule in `validate_plan()` that requires `bash scripts/verify.sh`
in acceptance (with WO-01 exemption) is replaced by R7 (reject it). The old
code path is removed when the new validator is activated.

---

## 10. Open Questions

### Q1: Should `file_absent` preconditions be required or optional?

For "create new file" WOs, `file_absent` preconditions are a safety net
against unintended overwrites. But requiring them adds verbosity. Consider
making them optional (recommended-but-not-enforced) initially.

### Q2: How precise should acceptance command dependency extraction be?

The `extract_file_dependencies` function handles `python -c`, `bash`, and
`python script.py` patterns. More exotic acceptance commands (e.g.,
`python -m mypackage.cli --help`) require additional patterns. Start simple
and extend as new patterns emerge from real planner output.

### Q3: Should the verify_contract be planner-declared or factory-configured?

This design has the planner declare the verify_contract as part of the
manifest. An alternative: the factory declares it as a static config (e.g.,
in a `factory_config.json`). The planner approach is more flexible (different
specs can have different verify strategies) but relies on the planner LLM
getting it right. The factory approach is more rigid but eliminates LLM error.

**Recommendation:** Start with planner-declared. If planner LLMs consistently
get verify_contract wrong, move it to factory config.

### Q4: What about the W2 LLM reviewer pass?

The W2 reviewer (described in TO_FIX.md) addresses *semantic* errors in
acceptance commands (wrong argument types, mismatched signatures). The
validator in this document addresses *structural* errors (missing files,
unsatisfied preconditions). They are complementary:

- This validator catches: "you can't import `mypackage.solver` because no WO
  creates that file."
- W2 catches: "you called `Solver(grid)` but the constructor takes `(rows, cols)`."

Implement this validator first (Phase 1–3), then W2 (Phase 4 or later).
The validator is deterministic and cheap; W2 requires LLM calls and is
better suited as a follow-on enhancement.
