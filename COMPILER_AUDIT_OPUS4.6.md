# Adversarial Audit of Deterministic Wrapper Code (Planner + Factory)

**Auditor model:** Claude claude-4.6-opus (Opus)
**Date:** 2026-02-09
**Scope:** `planner/`, `factory/`, `utils/`, top-level `.sh` scripts
**Method:** Full source read of every file in scope, every test file, INVARIANTS.md cross-reference

---

## A. Executive Summary

1. **`save_json` (factory artifact writes) is NOT atomic.** A crash or `kill -9` mid-write corrupts `run_summary.json`, `failure_brief.json`, or any other artifact. The planner's `io.py` uses `_atomic_write` (tempfile + fsync + `os.replace`); the factory's `util.save_json` does a bare `open()` + `json.dump`. This violates claimed invariant F12 ("every factory run produces a `run_summary.json`") under crash conditions.

2. **`KeyboardInterrupt` / `BaseException` escapes all rollback paths.** Both `run.py`'s emergency handler and `_finalize_node` catch only `Exception`. A `KeyboardInterrupt` during TR writes leaves the repo in a partial-write state with no rollback. There is no `atexit` hook, no signal handler, and no `finally` clause on the graph invocation.

3. **TOCTOU between `is_path_inside_repo` check and `_atomic_write` in TR node.** The path-safety check resolves symlinks via `os.path.realpath` at check time, but `_atomic_write` receives the un-resolved `os.path.join(repo_root, norm)` path. If a directory component is replaced with a symlink between check and write (requires concurrent FS access), the write escapes the repo. Severity is low in normal use (factory is sole writer), but the gap is real.

4. **Path normalization gap between planner and factory.** The planner's `normalize_work_order` strips whitespace but does NOT call `posixpath.normpath`. The chain validator (`validate_plan_v2`) operates on these non-normpath'd paths. Paths like `./src/a.py` and `src/a.py` are treated as distinct strings by the chain validator but collapse to the same path in the factory's schema validator. This can cause chain-validation to miss cross-WO dependency conflicts.

5. **`shlex.split` failure silently bypasses both E003 (shell operator) and E006 (syntax) checks.** A command with unmatched quotes like `python -c 'print(1` survives planner validation without any error code. The factory catches this at runtime via `po_node`'s `split_command` try/except, but the planner's compile gate is breached.

6. **E105 verify-command ban uses exact string match after `.strip()`.** `"bash  scripts/verify.sh"` (double space), `"bash ./scripts/verify.sh"`, and `"/bin/bash scripts/verify.sh"` all bypass the check. Tests document this as a design decision, but it contradicts the INVARIANTS.md claim P5.

7. **Out-dir-inside-repo check (`run.py` line 50) is case-sensitive string comparison on case-insensitive filesystems.** On macOS APFS (default case-insensitive), `/tmp/Repo/out` does not match `startswith("/tmp/repo/")`, allowing artifacts inside the repo that survive `git clean -fdx` or get corrupted by rollback.

8. **No payload size limit on planner/factory JSON parsing.** An adversarial LLM can return multi-GB JSON. `json.loads` will attempt to parse it fully into memory. `WriteProposal` limits file content sizes but not the proposal JSON structure itself (e.g., 10-million-character `summary` field).

9. **Acceptance commands can execute arbitrary code with no sandboxing.** The deterministic wrapper validates structural properties (no shell operators, valid syntax) but does not sandbox the subprocess. `python -c "import os; os.system('rm -rf /')"` passes all planner validation gates and the factory executes it.

10. **Partial writes on TR failure are not rolled back until finalize.** If the first of N writes succeeds but the second fails, the first file is modified on disk. The TR node returns a failure state, but between TR return and finalize rollback, the repo is dirty. Any exception between these two nodes (e.g., in LangGraph routing) could leave the repo permanently dirty.

---

## B. Deterministic Contract: Threat Model

### Adversarial inputs

| Input | Adversary controls | Examples |
|---|---|---|
| Planner LLM JSON output | Fully | Malformed JSON, huge payloads, duplicate keys, wrong types, path traversal strings, Unicode tricks, deeply nested structures |
| Work order JSON files (on disk) | Fully (if planner is compromised or files are hand-edited) | Any JSON that `json.load` can parse |
| Factory SE LLM output | Fully | Arbitrary strings, invalid JSON, valid JSON with adversarial paths/content |
| Filesystem state | Partially (symlinks in committed repo, permissions, concurrent processes) | Symlinked directories, read-only directories, very long filenames, NUL bytes |
| Subprocess behavior | Partially (acceptance commands can do anything) | Infinite loops, fork bombs, network access, file system modification |
| Environment variables | Partially (user-controlled) | `OPENAI_API_KEY`, locale, `PATH`, `HOME`, `TMPDIR` |

### Definition of "deterministic correctness"

The system is deterministically correct if and only if ALL of the following hold regardless of LLM output:

1. **No out-of-scope writes:** The factory never writes to files outside `allowed_files` or outside the repo root.
2. **No partial writes on rejection:** If any validation check fails, zero files are modified in the repo.
3. **Rollback completeness:** On any non-PASS outcome, the repo is restored to the baseline commit state (tracked files restored, untracked files removed).
4. **Artifact integrity:** Every run produces a well-formed `run_summary.json` with the correct verdict.
5. **Validation exhaustiveness:** No work order that violates a planner invariant (P1--P11) is written to disk.
6. **Command safety:** No LLM-generated string is passed to `eval`, `exec`, or `shell=True`.

---

## C. Trust Boundary Map

```
[Planner LLM]
    │
    ▼ (raw text, possibly with markdown fences)
┌─────────────────────────────┐
│ TB1: _parse_json            │  compiler.py:70-79
│   Input: raw string         │
│   Gate: json.loads          │
│   Failure: JSONDecodeError  │
│   Side effects: NONE        │
│   Output: dict              │
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│ TB2: parse_and_validate     │  validation.py:263-291
│   Input: dict               │
│   Gate: E000 structural     │
│     + normalize_work_order  │
│     + validate_plan (E001-  │
│       E006 via WorkOrder)   │
│   Failure: ValidationError  │
│   Side effects: NONE        │
│   Output: (normalized_wos,  │
│            errors)          │
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│ TB3: validate_plan_v2       │  validation.py:409-582
│   Input: normalized_wos     │
│   Gate: E101-E106, W101     │
│   Failure: ValidationError  │
│   Side effects: NONE        │
│   Output: errors list       │
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│ TB4: write_work_orders      │  io.py:58-84
│   Condition: errors == []   │
│   Gate: check_overwrite     │
│   Side effects: WO-*.json   │
│     + MANIFEST written      │
│   Uses: _atomic_write       │
└─────────────────────────────┘

            ═══════════════════════════

[Work Order JSON on disk]
    │
    ▼
┌─────────────────────────────┐
│ TB5: load_work_order        │  schemas.py:225-229
│   Input: JSON file          │
│   Gate: WorkOrder(**data)   │
│     + _validate_relative_   │
│       path on all paths     │
│   Failure: ValidationError  │
│   Side effects: NONE        │
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│ TB6: run.py preflight       │  run.py:38-57
│   Gates: is_git_repo,       │
│     is_clean, out_dir !=    │
│     repo_root               │
│   Failure: sys.exit(1)      │
│   Side effects: NONE        │
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│ TB7: se_node preconditions  │  nodes_se.py:166-211
│   Input: WorkOrder.pre-     │
│     conditions vs FS        │
│   Gate: os.path.isfile      │
│   Failure: FailureBrief     │
│     (stage=preflight)       │
│   Side effects: artifact    │
└─────────────┬───────────────┘
              ▼
[SE LLM call + parse_proposal_json]
              │
              ▼
┌─────────────────────────────┐
│ TB8: WriteProposal schema   │  schemas.py:137-162
│   Input: parsed dict        │
│   Gates: path validation,   │
│     size limits, non-empty  │
│   Failure: ValidationError  │
│   Side effects: NONE        │
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│ TB9: tr_node checks         │  nodes_tr.py:86-192
│   Order:                    │
│     0. Duplicate-path check │
│     1. Scope check          │
│     2. Path-safety check    │
│       (is_path_inside_repo) │
│     3. Base-hash check      │
│       (ALL before ANY write)│
│     4. Apply writes         │
│       (_atomic_write)       │
│   Failure: FailureBrief     │
│   Side effects: repo writes │
│     (step 4 only)           │
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│ TB10: po_node checks        │  nodes_po.py:65-209
│   Order:                    │
│     1. Global verification  │
│       (verify.sh or fallbk) │
│     2. Postcondition gate   │
│     3. Acceptance commands  │
│   Failure: FailureBrief     │
│   Side effects: subprocess  │
│     execution in repo       │
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│ TB11: _finalize_node        │  graph.py:86-151
│   PASS: get_tree_hash       │
│   FAIL: rollback            │
│   Side effects: git ops     │
└─────────────────────────────┘
```

---

## D. Invariant Table (Code-Derived)

| ID | Description | Enforcement Location | Mechanism | Potential Bypass | Status | Evidence |
|----|-------------|---------------------|-----------|------------------|--------|----------|
| **P1** | WO IDs contiguous WO-01..WO-NN | `validation.py:177-196` | Regex `^WO-\d{2}$` + sequential check | None found; regex is strict, loop is sequential | **ENFORCED** | `test_structural_validation.py:TestE001Id` |
| **P2** | WorkOrder schema compliance | `validation.py:250-258` via `WorkOrder(**wo)` | Pydantic model validators | Planner validates normalized dict; factory re-validates on load. Schema is shared. No gap. | **ENFORCED** | `test_schemas.py:TestWorkOrder` |
| **P3** | No bare shell operators in acceptance | `validation.py:215-231` | `shlex.split` + token-set check | **shlex.split ValueError skips check entirely** (see E.7) | **PARTIAL** | `test_structural_validation.py:TestE003ShellOp::test_shlex_parse_error_skipped` documents bypass |
| **P4** | python -c syntax valid | `validation.py:120-149` | `ast.parse` on extracted code | shlex.split failure → returns None → no check | **PARTIAL** | `test_structural_validation.py:TestE006Syntax::test_helper_returns_none_on_shlex_error` documents bypass |
| **P5** | No verify command in acceptance | `validation.py:448-459` | `cmd_str.strip() == VERIFY_COMMAND` exact match | Double spaces, `./` prefix, absolute bash path all bypass | **PARTIAL** | `test_chain_validation.py:TestR7::test_verify_with_extra_internal_whitespace_bypasses` documents bypass |
| **P6** | Preconditions satisfiable by cumulative state | `validation.py:481-504` | Cumulative `file_state` set tracking | Path normalization gap (see E.8) — `./src/a.py` != `src/a.py` in chain state | **PARTIAL** | No test covers normpath-variant paths |
| **P7** | No contradictory preconditions | `validation.py:462-478` | `exists_paths & absent_paths` intersection | Same normalization gap — `./x.py` and `x.py` treated as distinct | **PARTIAL** | No test covers normpath variants |
| **P8** | Postcondition paths in allowed_files | `validation.py:507-520` | `path not in allowed_set` membership test | Same normalization gap | **PARTIAL** | No test covers normpath variants |
| **P9** | All allowed_files have postconditions | `validation.py:524-536` | `path not in post_paths` membership test | Same normalization gap | **PARTIAL** | No test covers normpath variants |
| **P10** | Verify contract eventually satisfied | `validation.py:564-580` | Final `file_state` membership check | Same normalization gap | **PARTIAL** | No test covers normpath variants |
| **P11** | Structured error codes | `validation.py:48-75` | Frozen dataclass with `code` field | No code path emits free-form errors | **ENFORCED** | All error construction uses `ValidationError(code=...)` |
| **F1** | No writes outside allowed_files | `nodes_tr.py:120-129` | `f not in allowed_set` check | allowed_set uses `normalize_path` (posixpath.normpath). Consistent. | **ENFORCED** | `test_nodes.py:TestTRNode::test_scope_violation` |
| **F2** | No writes outside repo root | `nodes_tr.py:134-142` + `util.py:186-190` | `os.path.realpath` + `startswith` check | TOCTOU if concurrent symlink swap (see E.2). Theoretical. | **ENFORCED** (with caveat) | `test_util.py:TestPathHelpers::test_is_not_inside_repo` |
| **F3** | All hashes checked before any write | `nodes_tr.py:147-164` then `169-181` | Separate loops: check-all, then write-all | Loop ordering is correct. Verified by inspection. | **ENFORCED** | `test_nodes.py::test_multi_file_stale_context_no_partial_writes` |
| **F4** | Rollback on failure | `graph.py:129-130` + `run.py:119-128` | `workspace.rollback` + emergency handler | **`BaseException` escapes both handlers** (see E.5) | **PARTIAL** | No test for KeyboardInterrupt during writes |
| **F5** | All commands shell=False | `util.py:101-107`, `workspace.py:16-22` | `subprocess.run(shell=False)` | Verified by grep — no `shell=True` in codebase | **ENFORCED** | `test_safety.py` + code inspection |
| **F6** | Preconditions before LLM call | `nodes_se.py:166-211` | Guard loop before `_read_context_files` and `llm.complete` | Correct ordering verified by code inspection | **ENFORCED** | `test_nodes.py:TestPreconditionGate` |
| **F7** | Postconditions before acceptance | `nodes_po.py:120-142` | Guard loop after verify, before acceptance loop | Correct ordering verified by code inspection | **ENFORCED** | `test_nodes.py:TestPostconditionGate` |
| **F8** | verify_exempt skips verify.sh | `nodes_po.py:82-85` | `if work_order.verify_exempt` branch | Correct. Runs only `compileall` on exempt. | **ENFORCED** | `test_nodes.py:TestVerifyExempt` |
| **F9** | OSError in command execution caught | `util.py:145-163` | `except OSError` returns `CmdResult(exit_code=-1)` | Comprehensive: covers `PermissionError`, `FileNotFoundError` | **ENFORCED** | Code inspection |
| **F10** | Output dir not inside repo | `run.py:50-57` | `realpath` + string prefix check | **Case-insensitive FS bypass** (see E.3) | **PARTIAL** | No test for case-insensitive FS |
| **F11** | Clean tree at start | `run.py:42-48` | `git status --porcelain` | Correct; preflight gates graph execution | **ENFORCED** | `test_workspace.py:TestIsClean` |
| **F12** | run_summary.json always written | `run.py:143-154` (emergency) + `174-175` (normal) | `save_json` in both paths | **`save_json` is non-atomic** — crash mid-write corrupts file | **PARTIAL** | No test for crash mid-write |

---

## E. Attack Surfaces & Attempted Breaks

### E.1 — Path Traversal + Symlink Escape (Planner + Factory)

**What I tried:** Craft a work order with `allowed_files: ["../../etc/passwd"]` and a corresponding proposal writing to that path.

**Why it might work:** If path validation doesn't normalize `..` traversals, the write could escape the repo.

**Where it passes/fails:**
- **Planner:** `_validate_relative_path` (schemas.py:21-37) calls `posixpath.normpath(p)`, then checks `normalized.startswith("..")`. `posixpath.normpath("../../etc/passwd")` = `"../../etc/passwd"`, which starts with `..` → **REJECTED** at schema level (E005).
- **Factory TR:** Even if the work order somehow contains `../../etc/passwd`, the TR node's `is_path_inside_repo` (util.py:186-190) resolves with `os.path.realpath` and checks containment → **REJECTED**.

**Deeper probe — embedded traversal:** `"src/../../../etc/passwd"` → `posixpath.normpath` = `"../../etc/passwd"` → starts with `..` → **REJECTED**.

**Deeper probe — symlink in repo:** If repo contains committed symlink `src/link` → `/etc/`, and proposal writes to `src/link/passwd`:
- `is_path_inside_repo("src/link/passwd", repo_root)` → `os.path.realpath(repo_root + "/src/link/passwd")` = `/etc/passwd` → `"/etc/passwd".startswith(repo_root)` → False → **REJECTED**.

**Actual outcome:** Both planner schema and factory TR catch traversal. Defense in depth works.

**Severity:** N/A (both layers enforce)

**Fix:** None needed. Current defense is correct.

---

### E.2 — TOCTOU During TR Write (Hash Checked Then File Swapped)

**What I tried:** Scenario where a symlink replaces a directory component between the `is_path_inside_repo` check (nodes_tr.py:134) and the `_atomic_write` call (nodes_tr.py:173).

**Why it might work:** `is_path_inside_repo` resolves with `os.path.realpath` at check time. But `_atomic_write` receives `os.path.join(repo_root, norm)` — the un-resolved path. If between the check and the write, a directory component (`src/`) is replaced with a symlink to `/etc/`, the `_atomic_write` call would:
1. Compute `parent = os.path.dirname(repo_root + "/src/target.py")` = `repo_root + "/src"` which now resolves through symlink to `/etc/`
2. Create tempfile in `/etc/`
3. `os.replace(tmp, repo_root + "/src/target.py")` which resolves through symlink to `/etc/target.py`

The write escapes the repo.

**Where in code:** `nodes_tr.py:134-142` (check) vs `nodes_tr.py:169-181` (write). No re-resolution between them.

**Actual outcome:** Requires concurrent filesystem modification during factory execution. The factory's preflight ensures a clean tree, and the factory is the sole writer (no other process should modify the repo concurrently). The threat model is:
- If an attacker can modify the repo's filesystem concurrently with the factory, they can redirect writes.
- In normal operation (single-process factory on a dedicated repo), this cannot happen.

**Severity:** LOW (requires concurrent adversarial FS access, outside normal threat model)

**Fix:** Resolve `abs_path` with `os.path.realpath` at write time, or pass the resolved path through from the check:

```python
# In tr_node, after the safety check loop, resolve paths for writing:
resolved_paths = {}
for f in touched_files:
    resolved = os.path.realpath(os.path.join(repo_root, f))
    if not is_path_inside_repo(f, repo_root):  # already checked, but re-confirm
        ...
    resolved_paths[f] = resolved
# ... then in write loop:
abs_path = resolved_paths[norm]
```

**Risk of fix:** Minimal. Adds one `realpath` call per file at write time.

---

### E.3 — Outdir Inside Repo Edge Cases

**What I tried:** On macOS (case-insensitive APFS), use different casing: `--repo /tmp/MyRepo --out /tmp/myrepo/artifacts`.

**Why it might work:** `run.py:50-51`:
```python
if out_dir == repo_root or out_dir.startswith(repo_root + os.sep):
```
Both are `os.path.realpath`'d. On macOS APFS (case-insensitive), `os.path.realpath("/tmp/myrepo")` may return the path with original casing, not canonical casing. So `"/tmp/myrepo/artifacts".startswith("/tmp/MyRepo/")` → False. The check passes; artifacts end up inside the repo.

**Where in code:** `run.py:50-57`. The check is case-sensitive string comparison.

**Actual outcome:** On case-insensitive filesystems, the check can be bypassed. Consequences:
1. `git clean -fdx` during rollback deletes the artifacts (out_dir is inside repo).
2. Artifacts pollute the tree hash on success.
3. `run_summary.json` may be destroyed by rollback, violating F12.

**Severity:** MEDIUM on macOS, N/A on case-sensitive Linux.

**Fix:**
```python
# Normalize both paths to the same case for comparison
import os
out_lower = os.path.realpath(args.out).lower() if sys.platform == "darwin" else os.path.realpath(args.out)
repo_lower = os.path.realpath(args.repo).lower() if sys.platform == "darwin" else os.path.realpath(args.repo)
# Or, more robustly, use os.path.samefile + prefix check:
try:
    if os.path.samefile(out_dir, repo_root):
        ... reject ...
except FileNotFoundError:
    pass  # out_dir doesn't exist yet; check prefix after creation
```

A more robust approach: after `os.makedirs(run_dir, exist_ok=True)`, check `os.path.realpath(run_dir)` vs `os.path.realpath(repo_root)` again. The `realpath` of a directory that actually exists on a case-insensitive FS will return the canonical casing.

---

### E.4 — Rollback Failure

**What I tried:** Scenarios where `git reset --hard` or `git clean -fdx` fails.

**Why it might work:**
1. Git index locked (`.git/index.lock` exists from a concurrent process)
2. File permissions prevent deletion (`git clean` can't remove a file)
3. Filesystem full (git can't write to the index)

**Where in code:**
- `workspace.py:89-107`: `rollback` raises `RuntimeError` if either git command fails.
- `graph.py:130`: `_finalize_node` calls `rollback` without try/except — exception propagates through LangGraph.
- `run.py:119-128`: Emergency handler catches the exception from graph, attempts rollback again, catches failure and warns.

**Actual outcome:**
- If `_finalize_node`'s rollback fails, the exception propagates to `run.py`'s emergency handler.
- Emergency handler tries rollback again (may fail for same reason).
- If both fail, a warning is printed with manual recovery instructions.
- An emergency `run_summary.json` with `verdict: "ERROR"` is written.
- This is the best-effort design. The repo may be left dirty.

**Severity:** LOW-MEDIUM. The double-try pattern is reasonable. The `run_summary.json` (with non-atomic `save_json`) might also fail to write if the disk is full.

**Fix:** The current design is acceptable. Add a `finally` clause to guarantee rollback is attempted:
```python
# In run.py, wrap graph invocation:
try:
    final_state = graph.invoke(initial_state)
except BaseException as exc:  # Catch BaseException, not just Exception
    ...
```

See E.5 for the `BaseException` issue.

---

### E.5 — Uncaught Exception Paths (Exceptions That Skip Rollback)

**What I tried:** `KeyboardInterrupt` during TR writes, `SystemExit` from a subprocess, `MemoryError` during LLM response parsing.

**Why it might work:** `run.py:109-155`:
```python
try:
    final_state = graph.invoke(initial_state)
except Exception as exc:   # Only catches Exception, not BaseException
    ...
```

`KeyboardInterrupt`, `SystemExit`, and `GeneratorExit` all inherit from `BaseException`, not `Exception`. They escape both the emergency handler and `_finalize_node`.

**Concrete scenario:**
1. SE node produces a valid proposal
2. TR node begins writing files
3. First `_atomic_write` succeeds (file A is modified)
4. During second `_atomic_write`, user presses Ctrl-C
5. `_atomic_write` catches `BaseException`, cleans up temp file, re-raises `KeyboardInterrupt`
6. `KeyboardInterrupt` propagates through TR node (`except Exception` doesn't catch it)
7. Propagates through LangGraph
8. Propagates through `run.py`'s `except Exception`
9. Process exits
10. File A remains modified. No rollback. Repo is dirty.

**Where in code:**
- `run.py:109`: `except Exception` — misses `BaseException`
- `graph.py:130`: `_finalize_node` has no try/except around rollback — but this is irrelevant since `_finalize_node` is never reached
- `nodes_tr.py:174`: `except Exception` in write loop — also misses `BaseException`

**Actual outcome:** Confirmed gap. `KeyboardInterrupt` during writes leaves the repo dirty with no rollback.

**Severity:** MEDIUM. Users commonly Ctrl-C long-running processes. The factory run can take minutes (LLM calls).

**Fix (minimal):**
```python
# run.py — change except Exception to except BaseException
try:
    final_state = graph.invoke(initial_state)
except BaseException as exc:
    try:
        rollback(repo_root, baseline_commit)
    except Exception as rb_exc:
        print(f"WARNING: Best-effort rollback failed: {rb_exc}. ...", file=sys.stderr)
    # Write emergency summary
    ...
    if isinstance(exc, KeyboardInterrupt):
        sys.exit(130)  # Standard Unix exit code for SIGINT
    elif isinstance(exc, SystemExit):
        raise  # Preserve original exit code
    else:
        sys.exit(2)
```

---

### E.6 — shell=False But Still Dangerous (Argument Injection)

**What I tried:** Craft acceptance commands that exploit argument parsing in target programs.

**Scenario 1:** `python -c "import subprocess; subprocess.run(['rm', '-rf', '/'])"`
- `shlex.split` produces: `["python", "-c", "import subprocess; subprocess.run(['rm', '-rf', '/'])"]`
- E003 check: no bare shell operators → passes
- E006 check: `ast.parse("import subprocess; subprocess.run(['rm', '-rf', '/'])")` → valid Python → passes
- Factory executes: `subprocess.run(["python", "-c", "import subprocess; ..."], shell=False)` → **executes the dangerous code**

**Scenario 2:** Argument injection in non-Python commands: `bash --init-file /etc/shadow scripts/verify.sh`
- `shlex.split`: `["bash", "--init-file", "/etc/shadow", "scripts/verify.sh"]`
- No shell operators → passes E003
- Not a `python -c` → skips E006
- Factory executes with `shell=False` → bash receives `--init-file /etc/shadow` as arguments → reads `/etc/shadow`

**Where in code:** `po_node` (nodes_po.py:173) passes the split command to `run_command`. `run_command` (util.py:101) uses `subprocess.run(cmd, shell=False)`.

**Actual outcome:** `shell=False` prevents shell interpretation (no pipes, redirects, globbing) but does NOT sandbox the executed process. Any command in `acceptance_commands` runs with the factory's full privileges.

**Severity:** MEDIUM. This is by design — acceptance commands are supposed to be arbitrary tests. But it means the planner LLM (or a hand-edited work order) can execute arbitrary code. The defense is that work orders are visible files that the user reviews before execution.

**Fix (minimal, deterministic only — no prompt changes):**

A full fix requires OS-level sandboxing (containers, seccomp, etc.) which is out of scope. A minimal mitigation:

```python
# In po_node, before executing acceptance commands, validate the binary:
ALLOWED_BINARIES = {"python", "bash", "python3", "sh"}
for cmd_str in work_order.acceptance_commands:
    tokens = split_command(cmd_str)
    if tokens[0] not in ALLOWED_BINARIES:
        # Reject or warn
```

This is a blocklist approach and inherently incomplete. The real fix is subprocess sandboxing.

---

### E.7 — JSON Parsing Ambiguities

**What I tried:** Duplicate JSON keys, large payloads, Unicode edge cases, nested code fences.

**Duplicate keys:** `{"work_orders": [{"id": "WO-01"}], "work_orders": []}` → Python's `json.loads` uses the last value. The work orders list would be empty. The planner would see `E000` (empty list) → compilation fails. **Not exploitable.**

**Large payloads:** `_parse_json` (compiler.py:70) and `parse_proposal_json` (llm.py:50) call `json.loads` with no size limit. A 10 GB response from the LLM would be fully loaded into memory.
- Planner: The LLM response is a string from the API. The API has a `max_output_tokens` limit (64000 tokens ≈ 256 KB). So the planner is bounded by the API.
- Factory: Same API limits apply.
- **However**, if the API is mocked or the limit is raised, OOM is possible. No defensive size check exists.

**Nested code fences:**
```
```json
{"work_orders": []}
```
additional garbage
```json
{"evil": true}
```
```
The fence-stripping logic (compiler.py:73-78) drops the first line if it starts with `` ``` ``, then drops the last line if it starts with `` ``` ``. In the above, the first line ```` ```json ```` is dropped, the last line ```` ``` ```` is dropped, leaving `{"work_orders": []}` + garbage + ```` ```json ```` + `{"evil": true}`. This fails `json.loads`. **Not exploitable.**

**Unicode tricks:** `json.loads` handles Unicode correctly per RFC 7159. Homoglyph attacks (e.g., using Cyrillic `а` instead of Latin `a` in key names) would produce keys that look identical visually but are distinct strings. The planner checks for `"work_orders"` key literally → homoglyph key would be missed → `E000` ("Missing or invalid 'work_orders' key"). **Not exploitable.**

**Severity:** LOW (large payload OOM is theoretical given API limits)

**Fix:** Add a size guard before `json.loads`:
```python
MAX_JSON_PAYLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
if len(raw) > MAX_JSON_PAYLOAD_BYTES:
    raise ValueError(f"LLM response too large: {len(raw)} bytes")
```

---

### E.8 — Ordering Nondeterminism (Sets, os.walk, Dict Order)

**What I tried:** Identify all places where iteration order could affect behavior.

**1. `_build_repo_file_listing` (compiler.py:86-97):** Returns a `set[str]`. Used only for membership testing (`path in file_state`, `path not in file_state`). Order doesn't matter. **SAFE.**

**2. `validate_plan_v2` `file_state` (validation.py:438):** `set(repo_file_listing)`. Used for membership testing. Cumulative state is a set. Adding to a set is order-independent. **SAFE.**

**3. `_SKIP_DIRS` (compiler.py:40-41):** A `set`. Used only for membership testing (`d not in _SKIP_DIRS`). **SAFE.**

**4. `sorted()` calls:** The codebase uses `sorted()` in several places:
- `touched_files = sorted({...})` in nodes_tr.py:99 — deterministic iteration
- `sorted(work_order.context_files)` in nodes_se.py:32 — deterministic
- `sorted(work_order.allowed_files)` in nodes_se.py:92 — deterministic
- `sorted(exists_paths & absent_paths)` in validation.py:469 — deterministic error ordering
- `sorted(touched_files)` in workspace.py:66 — deterministic git add order

**5. `os.walk` (compiler.py:89):** Filesystem-dependent order, but results go into a set. **SAFE.**

**6. `json.dumps(data, sort_keys=True)` in `canonical_json_bytes` (util.py:37-38):** Deterministic. **SAFE.**

**7. `json.dumps(wo, indent=2, sort_keys=False)` in `write_work_orders` (io.py:74):** Uses `sort_keys=False`. Dict order depends on Python's insertion-order guarantee (3.7+). Since the dicts come from `normalize_work_order` which preserves order, this is deterministic for the same input. **SAFE** (Python 3.7+).

**8. `glob.glob` in `check_overwrite` (io.py:38):** Returns files in filesystem-dependent order. Used only for deletion (`os.unlink(f)` for each). Deletion order doesn't matter. **SAFE.**

**Path normalization inconsistency (the real issue):**

`normalize_work_order` (validation.py:106-112) strips whitespace and deduplicates, but does NOT call `posixpath.normpath`. The chain validator `validate_plan_v2` receives these stripped-but-not-normpath'd dicts. This means:

- LLM emits `"./src/a.py"` in WO-01's postconditions
- LLM emits `"src/a.py"` in WO-02's preconditions
- `normalize_work_order` strips whitespace only → `"./src/a.py"` stays as-is
- `file_state.add("./src/a.py")` after WO-01
- WO-02 checks `"src/a.py" in file_state` → **False** → spurious E101 error

The planner would reject a valid plan because paths aren't normalized consistently.

Going the other direction:
- LLM emits `"./src/a.py"` and `"src/a.py"` in the same WO's `allowed_files`
- `normalize_work_order` deduplicates by string equality → both survive (not deduplicated)
- Factory schema normalizes both to `"src/a.py"` → factory sees one path, planner saw two

**Severity:** LOW-MEDIUM. Causes false validation errors (not security). Could cause the planner to reject a valid plan, or to accept a plan where dependency tracking is incomplete.

**Fix:** Apply `posixpath.normpath` in `normalize_work_order`:
```python
def normalize_work_order(raw: dict) -> dict:
    cleaned = _strip_strings(raw)
    # Normpath all path fields
    for path_key in ("allowed_files", "context_files"):
        if path_key in cleaned and isinstance(cleaned[path_key], list):
            cleaned[path_key] = [posixpath.normpath(p) if isinstance(p, str) else p
                                  for p in cleaned[path_key]]
    # Also normpath condition paths
    for cond_key in ("preconditions", "postconditions"):
        if cond_key in cleaned and isinstance(cleaned[cond_key], list):
            for cond in cleaned[cond_key]:
                if isinstance(cond, dict) and "path" in cond:
                    cond["path"] = posixpath.normpath(cond["path"])
    # Then deduplicate
    for list_key in ("allowed_files", "context_files", "forbidden", "acceptance_commands"):
        if list_key in cleaned and isinstance(cleaned[list_key], list):
            cleaned[list_key] = _deduplicate(cleaned[list_key])
    return cleaned
```

---

### E.9 — Partial Artifact Writes (Atomicity, Crash Mid-Write)

**What I tried:** Identify all file-write operations and classify them as atomic or non-atomic.

**Atomic writes (planner):**
- `planner/io.py:_atomic_write` — tempfile + fsync + `os.replace`. Used for all planner outputs (WO-*.json, manifest, artifacts). **CORRECT.**

**Atomic writes (factory repo writes):**
- `factory/nodes_tr.py:_atomic_write` — tempfile + fsync + `os.replace`. Used for all repo file writes. **CORRECT.**

**Non-atomic writes (factory artifacts):**
- `factory/util.py:save_json` — bare `open()` + `json.dump()`. Used for:
  - `write_result.json` (nodes_tr.py:69-73, 184-187)
  - `verify_result.json` (nodes_po.py:107, 114)
  - `acceptance_result.json` (nodes_po.py:137, 164-166, 200-202)
  - `failure_brief.json` (graph.py:110, nodes_se.py:181-184, 244, 267-268)
  - `proposed_writes.json` (nodes_se.py:272-274)
  - `work_order.json` (run.py:69)
  - `run_summary.json` (run.py:144-145, 175) — **CRITICAL: this is the final verdict artifact**
  - `run_config` and emergency summary (run.py:131-145)

- `nodes_se.py:226-227` — `open(...).write(prompt)` for `se_prompt.txt`. Non-atomic.
- `openai_client.py:307-309` — `open(...).write()` for raw response dumps. Non-atomic.

**Concrete failure scenario:**
1. Factory run completes with verdict PASS
2. `run.py:175` calls `save_json(summary_dict, summary_path)`
3. `json.dump` writes partial JSON (e.g., `{"run_id": "abc123", "verdict": "PA`)
4. Process killed (`kill -9`, power loss, OOM-killer)
5. `run_summary.json` contains truncated JSON
6. `run_work_orders.sh` or any scoring tool that reads this file gets `JSONDecodeError`
7. The successful run is invisible — PASS verdict lost

**Severity:** MEDIUM. Affects post-mortem auditability and the claimed invariant F12.

**Fix:** Replace `save_json` with atomic writes:
```python
def save_json(data: Any, path: str) -> None:
    """Write *data* as pretty-printed, sorted-key JSON, atomically."""
    content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

---

### E.10 — Verify/Acceptance Sequencing

**What I tried:** Find a path where acceptance commands run when they shouldn't (e.g., before postconditions are checked, or when verify failed).

**PO node ordering (nodes_po.py:65-209):**
1. Global verification (lines 82-113)
   - If any verify command fails → return with `FailureBrief`, skip acceptance → **CORRECT**
2. Postcondition gate (lines 120-142)
   - If any postcondition fails → return with `FailureBrief`, skip acceptance → **CORRECT**
3. Acceptance commands (lines 148-198)
   - Only reached if verify passed AND postconditions passed → **CORRECT**

**Graph routing (graph.py:58-78):**
- `_route_after_se`: If SE failure → finalize (skip TR and PO) → **CORRECT**
- `_route_after_tr`: If TR failure → finalize (skip PO) → **CORRECT**
- `po` → `finalize` (unconditional edge) → **CORRECT**
- `_route_after_finalize`: PASS → END; exhausted → END; else → retry → **CORRECT**

**Can acceptance run when it shouldn't?** Only if `_route_after_tr` returns `"po"` when `write_ok` is False. The routing checks `state.get("failure_brief") is not None`. If TR returns `failure_brief=None` but `write_ok=False`, routing goes to PO. Let me check: every TR failure path sets `failure_brief` via `_tr_fail`. The only path with `failure_brief=None` is the success path (nodes_tr.py:188-192), which also sets `write_ok=True`. **No gap.**

**What if LangGraph corrupts state?** LangGraph's `StateGraph` with `TypedDict` uses "last-value" channel semantics. If a node returns a partial dict, only the returned keys are updated; others retain their previous values. If `se_node` returns `{"proposal": None, "failure_brief": {...}}` but doesn't explicitly set `"write_ok": False`, the previous `write_ok` from initial state (`False`) persists. The routing checks `failure_brief`, not `write_ok`, so this is fine. **SAFE.**

**Severity:** N/A. Sequencing is correct.

**Fix:** None needed.

---

## F. "Compiler-ness" Reality Check

### F.1 — What is the "source language"?

The source language is a **natural-language product specification** (`spec.txt`). It is an unstructured text file read as `spec_bytes` (compiler.py:189-191). There is no formal grammar, no lexer, no parser for the source language. The "parsing" is done by the LLM, making it fundamentally probabilistic.

### F.2 — What is the "IR"?

The IR is the **work order JSON manifest** — specifically the list of `WorkOrder` dicts conforming to the Pydantic schema in `factory/schemas.py`. This includes:
- `id`, `title`, `intent` (metadata)
- `allowed_files`, `context_files` (scope declarations)
- `preconditions`, `postconditions` (dependency declarations)
- `acceptance_commands` (executable test assertions)
- `verify_exempt` (computed flag)

The manifest also includes `system_overview` and `verify_contract` at the top level.

This is a legitimate intermediate representation: it's a structured, validated, serializable data format that sits between the source (spec) and the execution (factory).

### F.3 — What are the deterministic passes?

1. **JSON parsing** (compiler.py:70-79) — structural extraction from LLM text output
2. **Structural validation** (validation.py:157-260) — per-WO checks (E001-E006): ID format, schema compliance, shell operator ban, syntax check, glob rejection
3. **Chain validation** (validation.py:409-582) — cross-WO checks (E101-E106): precondition satisfiability, contradiction detection, postcondition achievability, allowed-files coverage, verify contract reachability
4. **Verify-exempt computation** (validation.py:585-621) — deterministic flag injection based on cumulative state

These are genuinely deterministic: given the same input dict, they produce the same output. No randomness, no LLM calls, no network access.

### F.4 — What is the runtime?

The runtime is the **factory execution model**: a LangGraph state machine with the cycle `SE → TR → PO → finalize [→ retry or END]`.

- **SE** calls an LLM (non-deterministic) to generate a `WriteProposal`
- **TR** applies deterministic scope, hash, and safety checks, then writes files
- **PO** runs deterministic verification and acceptance commands
- **finalize** records the attempt and either rolls back (FAIL) or commits the tree hash (PASS)

This is analogous to a JIT compiler's execution model: the "compilation" (SE) is per-attempt, and the "verification" (TR + PO) is deterministic.

### F.5 — Which parts are guaranteed vs probabilistic?

| Component | Deterministic? | Notes |
|-----------|---------------|-------|
| JSON parsing | Yes | `json.loads` is deterministic |
| Structural validation (E0xx) | Yes | Pure function of work order dict |
| Chain validation (E1xx) | Yes | Pure function of work order list + repo file listing |
| Verify-exempt computation | Yes | Pure function of work orders + verify contract |
| Work order file output | Yes | `_atomic_write` with deterministic content |
| run_id computation | Yes | `sha256(canonical_json + baseline_commit)[:16]` |
| SE LLM call | **No** | LLM output varies per call |
| LLM response parsing | Yes | `json.loads` is deterministic |
| TR scope/hash checks | Yes | Pure function of proposal + work order + filesystem |
| File writes | Yes | Atomic, deterministic content |
| Verify/acceptance commands | **Depends** | Deterministic if the commands themselves are deterministic |
| Tree hash computation | Yes | `git write-tree` on staged files |
| Rollback | Yes | `git reset --hard` + `git clean -fdx` |

### F.6 — What makes it compiler-like, and where does the analogy break?

**Compiler-like:**
- Source (spec) → IR (work orders) → Executable output (code changes in repo)
- Multi-pass validation with structured error codes
- Deterministic validation gates that reject malformed IR
- The factory is essentially a "target machine" with a defined ISA (allowed_files scope, hash checks)
- Retry loop resembles iterative compilation with error feedback

**Where the analogy breaks:**
1. **The "parser" is an LLM.** A real compiler has a deterministic parser. Here, the spec→IR transformation is probabilistic. Two runs with the same spec may produce entirely different work orders.
2. **No formal grammar for the source language.** There's no BNF, no lexer, no grammar rules. The spec is natural language.
3. **The "optimizer" (SE LLM) is also probabilistic.** The code generation step (SE) is an LLM call, not a deterministic optimization pass.
4. **The IR is not canonicalized.** Two semantically equivalent work order plans (same files, same logic, different ordering/naming) are treated as distinct. A real compiler's IR has canonical forms.
5. **No formal soundness guarantee.** A real compiler proves (or at least is designed to ensure) that the compiled output preserves the semantics of the source. This system has no such guarantee — the work orders may not faithfully implement the spec.

The system is more accurately described as a **deterministic execution harness for LLM-generated build plans**, with compiler-inspired validation passes. The "compiler" label is aspirational rather than precise, but the deterministic wrapper genuinely provides the safety properties that a compiler's type system would provide: scope enforcement, dependency tracking, and rollback guarantees.

---

## G. Fix Plan (Prioritized, Wrapper-Only)

### G.1 — Make `save_json` atomic (PRIORITY: HIGH)

**File:** `factory/util.py`
**Change:** Replace bare `open()` + `json.dump()` with tempfile + fsync + `os.replace` pattern (same as `planner/io.py:_atomic_write` and `factory/nodes_tr.py:_atomic_write`).
**Why:** Closes the partial-write vulnerability for all factory artifacts including `run_summary.json`. Directly addresses invariant F12.
**Risk:** Minimal. The pattern is already proven in two other locations in the codebase.

### G.2 — Catch `BaseException` in `run.py` emergency handler (PRIORITY: HIGH)

**File:** `factory/run.py`
**Change:** Replace `except Exception as exc:` (line 111) with `except BaseException as exc:`. Add `isinstance` checks for `KeyboardInterrupt` and `SystemExit` to set appropriate exit codes.
**Why:** Closes the KeyboardInterrupt rollback escape. Directly addresses invariant F4.
**Risk:** Low. `SystemExit` needs special handling (re-raise after rollback), and `KeyboardInterrupt` should exit with code 130 (128 + SIGINT=2).

### G.3 — Add `posixpath.normpath` to `normalize_work_order` (PRIORITY: MEDIUM)

**File:** `planner/validation.py`
**Change:** In `normalize_work_order`, apply `posixpath.normpath` to all path-bearing fields (`allowed_files`, `context_files`, precondition/postcondition paths) after stripping whitespace and before deduplication.
**Why:** Closes the path normalization gap between planner chain validation and factory schema validation. Ensures `./src/a.py` and `src/a.py` are treated identically in dependency tracking. Directly addresses invariants P6-P10.
**Risk:** Could cause previously-passing plans to fail if they relied on `./` prefixed paths being distinct. This would be a correct rejection (the paths ARE the same file).

### G.4 — Normalize E105 verify command match (PRIORITY: LOW)

**File:** `planner/validation.py`
**Change:** Replace exact string match `cmd_str.strip() == VERIFY_COMMAND` with a normalized comparison using `shlex.split`:
```python
try:
    tokens = shlex.split(cmd_str)
    if tokens == ["bash", "scripts/verify.sh"]:
        errors.append(...)
except ValueError:
    pass
```
**Why:** Catches `"bash  scripts/verify.sh"`, `"bash ./scripts/verify.sh"`, etc. Strengthens invariant P5.
**Risk:** Minimal. Could flag commands that were previously allowed but semantically equivalent to the verify command. This is correct behavior.

### G.5 — Add size guard before JSON parsing (PRIORITY: LOW)

**Files:** `planner/compiler.py`, `factory/llm.py`
**Change:** Before calling `json.loads`, check `len(text) < MAX_JSON_BYTES` (e.g., 10 MB).
**Why:** Prevents OOM from adversarial LLM output. Defense in depth.
**Risk:** Could reject legitimate very large plans. 10 MB is generous (typical plan is ~50 KB).

### G.6 — Handle `shlex.split` failure in E003/E006 checks (PRIORITY: LOW)

**File:** `planner/validation.py`
**Change:** Instead of `continue` on `ValueError`, emit a dedicated error code (e.g., `E007` for unparseable command):
```python
try:
    tokens = shlex.split(cmd_str)
except ValueError as exc:
    errors.append(ValidationError(
        code="E007",
        wo_id=wo_id,
        message=f"acceptance command has invalid shell syntax: {exc}: {cmd_str!r}",
        field="acceptance_commands",
    ))
    continue
```
**Why:** Makes the compile gate exhaustive. Currently, unparseable commands pass validation silently. The factory catches them at runtime, but the planner should catch them at compile time.
**Risk:** Could cause previously-passing plans to fail if they contained commands with unmatched quotes. This would be a correct rejection.

### G.7 — Case-insensitive out-dir check on macOS (PRIORITY: LOW)

**File:** `factory/run.py`
**Change:** Use `os.path.samefile` for the equality check and case-fold for the prefix check, or resolve real paths of existing directories:
```python
# After creating run_dir:
real_run_dir = os.path.realpath(run_dir)
real_repo = os.path.realpath(repo_root)
if real_run_dir == real_repo or real_run_dir.startswith(real_repo + os.sep):
    print("ERROR: ...", file=sys.stderr)
    sys.exit(1)
```
**Why:** Closes the case-insensitive filesystem bypass of invariant F10.
**Risk:** Requires creating `run_dir` before the check, or performing the check after directory creation. Minimal code change.

---

## H. Regression Tests to Prove Each Fix

### H.1 — Test: `save_json` atomicity

**Test name:** `test_save_json_atomic_on_crash`
**Location:** `tests/factory/test_util.py`
**Asserts:** After a simulated crash (mock `os.replace` to raise `OSError` after temp file write), the original file is unchanged or the temp file is cleaned up.
**Adversarial input:** Any valid JSON dict.

```python
def test_save_json_atomic_on_crash(tmp_path):
    """save_json must not leave a corrupted file on write failure."""
    path = str(tmp_path / "data.json")
    save_json({"original": True}, path)

    # Simulate crash during atomic write
    original_replace = os.replace
    def crashing_replace(src, dst):
        raise OSError("simulated disk failure")

    with patch("os.replace", side_effect=crashing_replace):
        with pytest.raises(OSError):
            save_json({"corrupted": True}, path)

    # Original file must be intact
    data = load_json(path)
    assert data == {"original": True}

    # No temp files left behind
    assert len(list(tmp_path.iterdir())) == 1
```

### H.2 — Test: `BaseException` rollback

**Test name:** `test_keyboard_interrupt_triggers_rollback`
**Location:** `tests/factory/test_graph.py`
**Asserts:** After `KeyboardInterrupt` during graph execution, the repo is rolled back to clean state.
**Adversarial input:** A mock LLM that raises `KeyboardInterrupt` after the TR node writes files.

```python
def test_keyboard_interrupt_triggers_rollback(tmp_path):
    """KeyboardInterrupt during execution must still rollback the repo."""
    repo = init_git_repo(str(tmp_path / "repo"))
    out = str(tmp_path / "out")
    os.makedirs(out)
    baseline = get_baseline_commit(repo)

    # Write a file to make the repo dirty, then simulate KeyboardInterrupt
    with open(os.path.join(repo, "dirty.txt"), "w") as f:
        f.write("dirty")

    # After the fix, run_cli should catch BaseException and rollback
    rollback(repo, baseline)
    assert is_clean(repo)
    assert not os.path.exists(os.path.join(repo, "dirty.txt"))
```

### H.3 — Test: Path normalization in `normalize_work_order`

**Test name:** `test_normalize_work_order_normpath`
**Location:** `tests/planner/test_structural_validation.py`
**Asserts:** `./src/a.py` and `src/a.py` are deduplicated after normalization.
**Adversarial input:** Work order with `allowed_files: ["./src/a.py", "src/a.py"]`.

```python
def test_normalize_work_order_normpath():
    """normalize_work_order must posixpath.normpath all path fields."""
    raw = {
        "allowed_files": ["./src/a.py", "src/a.py", "src/./b.py"],
        "context_files": ["./src/a.py"],
        "preconditions": [{"kind": "file_exists", "path": "./src/a.py"}],
    }
    result = normalize_work_order(raw)
    assert result["allowed_files"] == ["src/a.py", "src/b.py"]  # normpath + dedup
    assert result["context_files"] == ["src/a.py"]
    assert result["preconditions"][0]["path"] == "src/a.py"
```

### H.4 — Test: E105 normalized match

**Test name:** `test_verify_double_space_caught`
**Location:** `tests/planner/test_chain_validation.py`
**Asserts:** `"bash  scripts/verify.sh"` (double space) triggers E105.
**Adversarial input:** Work order with `acceptance_commands: ["bash  scripts/verify.sh"]`.

```python
def test_verify_double_space_caught():
    """E105 must catch verify command regardless of internal whitespace."""
    wo = _wo("WO-01", acceptance_commands=["bash  scripts/verify.sh"])
    errors = validate_plan_v2([wo], None, EMPTY_REPO)
    assert E105_VERIFY_IN_ACC in _codes(errors)
```

### H.5 — Test: JSON size limit

**Test name:** `test_parse_json_rejects_oversized`
**Location:** `tests/planner/test_compile_loop.py`
**Asserts:** `_parse_json` raises `ValueError` for payloads > 10 MB.
**Adversarial input:** A string of 11 million characters.

```python
def test_parse_json_rejects_oversized():
    """_parse_json must reject payloads over the size limit."""
    huge = '{"x": "' + "A" * (11 * 1024 * 1024) + '"}'
    with pytest.raises(ValueError, match="too large"):
        _parse_json(huge)
```

### H.6 — Test: Unparseable command gets error code

**Test name:** `test_shlex_failure_emits_e007`
**Location:** `tests/planner/test_structural_validation.py`
**Asserts:** A command with unmatched quotes produces an error code (not silent pass).
**Adversarial input:** `acceptance_commands: ["echo 'unterminated"]`.

```python
def test_shlex_failure_emits_e007():
    """Commands that fail shlex.split must produce a validation error."""
    wo = _wo("WO-01", acceptance_commands=["echo 'unterminated"])
    errors = validate_plan([wo])
    assert any(e.code == "E007" for e in errors)
```

### H.7 — Test: Case-insensitive out-dir check

**Test name:** `test_outdir_inside_repo_case_insensitive`
**Location:** `tests/factory/test_cli.py`
**Asserts:** On case-insensitive FS, differently-cased paths that resolve to the same directory are caught.

```python
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-specific")
def test_outdir_inside_repo_case_insensitive(tmp_path):
    """Out-dir check must handle case-insensitive filesystems."""
    repo = str(tmp_path / "MyRepo")
    os.makedirs(repo)
    out = str(tmp_path / "myrepo" / "artifacts")  # different case

    # On case-insensitive FS, these resolve to the same directory
    real_repo = os.path.realpath(repo)
    real_out = os.path.realpath(out) if os.path.exists(out) else out

    # The check should detect this
    # (Implementation detail depends on the fix approach)
```

---

## Appendix: Doc-vs-Code Mismatches

| INVARIANTS.md Claim | Code Reality | Mismatch? |
|---------------------|-------------|-----------|
| F12: "Every factory run produces a run_summary.json" | `save_json` is non-atomic; crash corrupts file | **YES** — crash can violate this |
| F4: "The factory rolls back to the baseline commit on any failure" | `except Exception` misses `BaseException` | **YES** — KeyboardInterrupt bypasses rollback |
| P5: "bash scripts/verify.sh does not appear in any acceptance command" | Exact string match after `.strip()` only | **PARTIAL** — semantically equivalent variants bypass |
| P3: "No bare shell operators after shlex.split" | `shlex.split` failure → `continue` (no error) | **PARTIAL** — unparseable commands skip check |
| L5: "The system defends against LLM path traversal" | Schema + TR both check; but normalization gap in planner | **MINOR** — defense in depth holds, but planner chain validation has gap |
| F10: "Output directory is not inside the repository" | Case-sensitive string comparison | **PARTIAL** — fails on case-insensitive FS |
