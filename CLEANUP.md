# CLEANUP.md — Behavior-Preserving Entropy Reduction Plan for `./factory`

## 0. Executive Summary (≤ 10 bullets)

- `./factory` is a ~750-line deterministic SE→TR→PO harness built on LangGraph. It is structurally sound but has measurable entropy in four areas: duplicated state-unpacking boilerplate, an unused Pydantic schema (`RunSummary`), fragmented FailureBrief construction, and zero dependency-injection seams.
- Every node (`se_node`, `tr_node`, `po_node`, `_finalize_node`) independently unpacks the same ~6 fields from a raw `dict`, recomputes `attempt_dir`, and re-instantiates `WorkOrder` from dict. This quadruplication is the single largest source of structural noise.
- ~~`nodes_tr.py` contains five near-identical blocks that construct a `FailureBrief`, persist a `write_result.json`, and return a failure dict. These differ only in `stage`, `primary_error_excerpt`, and `constraints_reminder`.~~ **RESOLVED (Phase 1):** Extracted `_tr_fail()` helper.
- ~~The `RunSummary` Pydantic model in `schemas.py` is defined but never instantiated—`run.py` builds the summary as a raw dict with extra keys (`config`, `error`, `error_traceback`) absent from the schema.~~ **RESOLVED (Phase 1):** `RunSummary` removed from `schemas.py`.
- **Safe cleanup**: extract shared state-unpacking, consolidate TR failure paths, reconcile or remove `RunSummary`, introduce minimal injection points for subprocess/LLM/filesystem calls.
- **Explicitly unsafe**: changing exception types or messages, altering exit codes, modifying the LangGraph routing topology, changing artifact filenames or JSON shapes, altering the `_git()` subprocess invocation pattern.
- The codebase has no module-level side effects on import and no hidden global state—these are strong invariants to preserve.
- `workspace.py` uses its own `subprocess.run()` wrapper (`_git`) separate from `util.run_command()`. This is intentional (git ops don't need artifact logging) but creates a dual-path subprocess story that complicates testing.
- `llm.py` reads `OPENAI_API_KEY` from `os.environ` inside `_get_client()` and instantiates a new `openai.OpenAI()` on every `complete()` call. This is the only non-CLI environment variable read.
- The `FactoryState` TypedDict in `graph.py` is defined with `total=False` but no node uses it for typing—all accept/return `dict`.

---

## 1. Behavioral Surface Area Map (Factory Only)

### Public Entrypoints

| Entrypoint | File | Mechanism |
|---|---|---|
| `python -m factory run ...` | `__main__.py` → `run.py:run_cli()` | argparse subcommand |
| `python -m factory --help` | `__main__.py:main()` | prints help, exits 1 if no subcommand |

### CLI Behavior Owned by `factory`

- `__main__.py` parses args, validates `--max-attempts >= 1`, calls `run.run_cli(args)`.
- `run.py:run_cli()` performs preflight (git repo, clean tree, out-dir safety), computes `run_id`, builds LangGraph initial state, invokes graph, writes `run_summary.json`.

### Observable Side Effects

**stdout**:
- On success: `Verdict: PASS\nRun summary: <path>`
- On failure: `Verdict: FAIL\nRun summary: <path>`
- On unhandled exception: nothing to stdout (all goes to stderr)
- On `--help` or missing subcommand: argparse help text

**stderr**:
- Preflight errors: `ERROR: ...` lines (from `run.py`)
- `--max-attempts < 1`: `ERROR: --max-attempts must be at least 1.`
- Unhandled exception: `Verdict: ERROR (unhandled exception)`, `Exception: ...`, `Run summary: ...`
- Failed rollback warning: `WARNING: Best-effort rollback failed: ...`
- Failed summary write: `CRITICAL: Failed to write run summary: ...`

**Filesystem writes** (all under `<out>/<run_id>/`):
- `work_order.json`
- Per attempt under `attempt_<N>/`:
  - `se_prompt.txt`, `proposed_writes.json`, `raw_llm_response.json` (on parse failure)
  - `write_result.json`
  - `verify_result.json`, `verify_<i>_stdout.txt`, `verify_<i>_stderr.txt`
  - `acceptance_result.json`, `acceptance_<i>_stdout.txt`, `acceptance_<i>_stderr.txt`
  - `failure_brief.json` (on failure)
- `run_summary.json`

**Product repo writes** (in-situ):
- Atomic file writes to `allowed_files` paths (via `nodes_tr._atomic_write`)
- `git reset --hard` + `git clean -fdx` on failure
- `git add` + `git write-tree` on success (to compute tree hash)

**Network**:
- Single outbound HTTPS call to OpenAI API per attempt (from `llm.complete()`)

**Environment variable reads**:
- `OPENAI_API_KEY` (in `llm._get_client()`)

**Subprocesses spawned** (all with `shell=False`):
- Git commands via `workspace._git()`: `rev-parse`, `status --porcelain`, `reset --hard`, `clean -fdx`, `add`, `write-tree`
- Verification/acceptance commands via `util.run_command()`

### Error Surfaces

**Exit codes**:
- `0`: PASS
- `1`: FAIL, preflight error, invalid `--max-attempts`, work-order load failure
- `2`: Unhandled exception during graph execution

**Exception types raised to caller**:
- `RuntimeError` from `workspace.py` (git failures in `get_baseline_commit`, `get_tree_hash`, `rollback`)
- `RuntimeError` from `llm.py` (missing API key, missing `openai` package, None LLM response)
- `ValueError` from Pydantic validators in `schemas.py`
- `SystemExit` from `sys.exit()` calls in `run.py` and `__main__.py`

**Sentinel return values**:
- `write_ok: bool` in TR node return dict
- `failure_brief: dict | None` in node return dicts
- `proposal: dict | None` in SE node return dict
- `verdict: str` ("PASS", "FAIL", "ERROR", or `""`)

---

## 2. Artifact Organization Findings (Factory Only)

### 2.1 Current State

`./factory` contains **zero** data files, schemas, templates, fixtures, logs, or intermediate outputs. It is purely Python source code (11 `.py` files). All artifacts are written at runtime to the user-specified `--out` directory, never inside `./factory` itself.

The artifact output structure is implicitly defined across four files:

| Artifact path pattern | Created in |
|---|---|
| `<out>/<run_id>/work_order.json` | `run.py:run_cli()` line 64 |
| `<out>/<run_id>/attempt_<N>/se_prompt.txt` | `nodes_se.py:se_node()` line 171 |
| `<out>/<run_id>/attempt_<N>/proposed_writes.json` | `nodes_se.py:se_node()` line 216 |
| `<out>/<run_id>/attempt_<N>/raw_llm_response.json` | `nodes_se.py:se_node()` line 208 |
| `<out>/<run_id>/attempt_<N>/write_result.json` | `nodes_tr.py:tr_node()` lines 79–83, 102–106, 123–126, 153–157, 190–193 |
| `<out>/<run_id>/attempt_<N>/failure_brief.json` | `nodes_se.py` line 188, `graph.py:_finalize_node()` line 104 |
| `<out>/<run_id>/attempt_<N>/verify_<i>_{stdout,stderr}.txt` | `nodes_po.py:po_node()` line 60–61 |
| `<out>/<run_id>/attempt_<N>/verify_result.json` | `nodes_po.py:po_node()` lines 78, 85 |
| `<out>/<run_id>/attempt_<N>/acceptance_<i>_{stdout,stderr}.txt` | `nodes_po.py:po_node()` line 120–121 |
| `<out>/<run_id>/attempt_<N>/acceptance_result.json` | `nodes_po.py:po_node()` lines 108, 148 |
| `<out>/<run_id>/run_summary.json` | `run.py:run_cli()` lines 138, 170 |

### 2.2 Problems Caused by Current Layout

1. ~~**Artifact path construction is scattered and implicit**. The string `f"attempt_{attempt_index}"` is computed independently in `se_node`, `tr_node`, `po_node`, and `_finalize_node` (4 places). Filenames like `"write_result.json"`, `"failure_brief.json"`, `"proposed_writes.json"` are string literals scattered across modules. There is no single manifest of artifact paths.~~ **RESOLVED (Phase 2):** `make_attempt_dir()` helper and 9 `ARTIFACT_*` constants added to `util.py`. All consumers updated.

2. **`failure_brief.json` is written in two different places**: SE node (write-ahead for crash resilience) and `_finalize_node` (canonical write). This is intentional (see Advisory §1B). **Clarified (Phase 2):** Write-ahead comments added to both SE write sites; finalize write site annotated with overwrite semantics.

3. **`attempt_dir` is created via `os.makedirs(..., exist_ok=True)` in all four nodes**. Since SE always runs first, the subsequent `makedirs` calls in TR, PO, and finalize are redundant but harmless.

### 2.3 Safe Cleanup Actions (Behavior-Preserving)

**Action 2.3.1: Centralize artifact path constants** — **DONE (Phase 2)**

Added to `util.py`: `make_attempt_dir(out_dir, run_id, attempt_index)` helper and 9 `ARTIFACT_*` string constants (`ARTIFACT_SE_PROMPT`, `ARTIFACT_PROPOSED_WRITES`, `ARTIFACT_RAW_LLM_RESPONSE`, `ARTIFACT_WRITE_RESULT`, `ARTIFACT_VERIFY_RESULT`, `ARTIFACT_ACCEPTANCE_RESULT`, `ARTIFACT_FAILURE_BRIEF`, `ARTIFACT_WORK_ORDER`, `ARTIFACT_RUN_SUMMARY`). All consumers in `nodes_se.py`, `nodes_tr.py`, `nodes_po.py`, `graph.py`, and `run.py` updated to use these. Dynamic log filenames (`verify_N_stdout.txt`, etc.) left inline in `nodes_po.py` since they only appear in one file.

**Action 2.3.2: Create `attempt_dir` once, pass it through state**

- **Files involved**: `graph.py` (add `attempt_dir` to `FactoryState`), `run.py` (or a pre-SE setup), all node files (read `attempt_dir` from state instead of computing it).
- **Proposed change**: Compute `attempt_dir` once (in the graph before SE runs, or in `_finalize_node` when incrementing `attempt_index`) and store it in state. Nodes read it instead of recomputing.
- **Why behavior is unchanged**: The resulting path string is identical; only the computation location changes.
- **Regression check**: Diff artifact directory structure between before/after runs.

**Action 2.3.3: Remove duplicate `failure_brief.json` write from SE node** — **CANCELLED (Advisory §1B)**

Reclassified as write-ahead resilience, not entropy. SE writes eagerly in case the process is killed before finalize runs. Write-ahead comments added in Phase 2 to document intent. See Advisory §1B for full crash/kill analysis.

🚫 **Unsafe artifact moves**:
- Moving `se_prompt.txt` or `raw_llm_response.json` writes out of SE node would change the point-in-time at which they are persisted. If the process crashes between SE and finalize, these artifacts would be lost. **Do not move.**

---

## 3. Exception Taxonomy & Error Handling Audit (Factory Only)

### 3.1 Observed Exception Patterns

**Custom exceptions defined**: None. The codebase uses only `RuntimeError`, `ValueError`, and `Exception`.

**`RuntimeError` usage** (all with specific messages):

| Location | Trigger |
|---|---|
| `workspace.py:get_baseline_commit()` line 46 | `git rev-parse HEAD` fails |
| `workspace.py:get_tree_hash()` lines 69, 75 | `git add` or `git write-tree` fails |
| `workspace.py:rollback()` lines 95, 101 | `git reset --hard` or `git clean -fdx` fails |
| `llm.py:_get_client()` line 17 | `OPENAI_API_KEY` not set |
| `llm.py:_get_client()` line 25 | `openai` package not installed |
| `llm.py:complete()` line 46 | LLM returned `None` content |

**`ValueError` usage**: All from Pydantic validators in `schemas.py`. These propagate as `pydantic.ValidationError` at construction time.

**Broad `except Exception` blocks**:

| Location | What it catches | What it does |
|---|---|---|
| `nodes_se.py:se_node()` line 182 | LLM API call failure | Wraps in `FailureBrief(stage="exception")`, returns failure dict |
| `nodes_se.py:se_node()` line 195 | JSON parse / Pydantic validation | Wraps in `FailureBrief(stage="llm_output_invalid")`, returns failure dict |
| `nodes_tr.py:tr_node()` line 172 | Atomic write failure | Wraps in `FailureBrief(stage="write_failed")`, returns failure dict |
| `run.py:run_cli()` line 106 | Graph invocation failure | Emergency rollback + summary, `sys.exit(2)` |
| `run.py:run_cli()` line 116 | Rollback itself failing | Warning to stderr, continues |
| `run.py:run_cli()` line 141 | Summary write failure | `CRITICAL` message to stderr |

**Places where exceptions are swallowed**:
- `run.py` line 141: Inner `except Exception` in the emergency handler prints to stderr but does not re-raise. This is intentional (best-effort).
- `nodes_tr.py:_atomic_write()` line 36: `except BaseException` catches to clean up temp file, then re-raises. Not swallowed.

### 3.2 Entropy Analysis

1. **No domain exception hierarchy**. All exceptional conditions are mapped to either `RuntimeError` (infrastructure) or `FailureBrief` (structured failure). The `FailureBrief.stage` field effectively serves as the exception taxonomy. This is not inherently wrong, but it means:
   - A caller cannot catch "git failure" distinctly from "LLM API failure" via exception type.
   - In `run.py:run_cli()` line 106, the broad `except Exception` must handle git failures, LLM failures, LangGraph internal errors, and `KeyError`s from malformed state all identically.

2. ~~**`FailureBrief` construction is duplicated extensively in `nodes_tr.py`**. There are five instances of the identical save+return boilerplate differing only in `stage`, `primary_error_excerpt`, and `constraints_reminder`.~~ **RESOLVED (Phase 1):** Extracted `_tr_fail()` helper in `nodes_tr.py`. Five call sites now delegate to a single function. `save_json` uses `sort_keys=True`, so dict insertion order is irrelevant — output is byte-identical.

3. ~~**Combined error excerpt construction duplicated in `nodes_po.py`**.~~ **RESOLVED (Phase 1):** Extracted `_combined_excerpt(cr: CmdResult)` helper in `nodes_po.py`. Byte-equivalence verified for all four cases (both streams, stderr-only, stdout-only, neither).

4. **Inconsistent truncation in SE node**. `nodes_se.py` line 199 uses `raw[:500]` (a raw slice), while the same file imports and uses `truncate()` from `util.py` on line 184 and 198. The `truncate()` function uses a 2000-char limit with a `...[truncated]` marker. The `raw[:500]` is a different, ad-hoc truncation.

### 3.3 Safe Consolidation Strategy

**3.3.1: Extract TR failure-return helper** — **DONE (Phase 1)**

Implemented `_tr_fail(stage, excerpt, reminder, touched_files, attempt_dir)` in `nodes_tr.py`. Five inline failure blocks replaced with single-line `return _tr_fail(...)` calls. Byte-identical output verified (see Advisory §2).

**3.3.2: Extract combined-error-excerpt helper in PO** — **DONE (Phase 1)**

Implemented `_combined_excerpt(cr: CmdResult)` in `nodes_po.py`. Two call sites (verify_failed, acceptance_failed) now use `truncate(_combined_excerpt(cr))`. Byte-equivalence verified for all edge cases (see Advisory §2).

**3.3.3: Normalize truncation in SE node**

Replace `raw[:500]` at `nodes_se.py` line 199 with `truncate(raw, max_chars=500)` to use the standard truncation function with its `...[truncated]` marker.

⚠️ **Tempting but unsafe**:
- Changing `RuntimeError` to custom exception types in `workspace.py` or `llm.py` would alter what `except RuntimeError` blocks catch elsewhere, and any external caller matching on exception type would break.
- Changing `FailureBrief.stage` string values would alter artifact content and LLM retry prompts.
- Changing the error message format in `FailureBrief.primary_error_excerpt` could alter retry behavior (the LLM reads these excerpts).

---

## 4. Test Seam Opportunities (Conceptual Only, Factory Scope)

### 4.1 LLM Call (`llm.py`)

**Current**: `nodes_se.py:se_node()` calls `llm.complete()` which calls `_get_client()` which reads `os.environ["OPENAI_API_KEY"]` and instantiates `openai.OpenAI()`. No injection point exists.

**Conceptual seam**: Accept an optional callable `complete_fn` parameter (defaulting to `llm.complete`) in `se_node`, or make `llm.complete` delegate to a module-level reference that can be replaced in tests. The simplest approach: tests can `unittest.mock.patch("factory.llm.complete")` today with no code changes, but this relies on import-path coupling.

**Behavior preservation**: The default path calls `llm.complete()` exactly as today. The seam only activates when a test provides a substitute.

### 4.2 Subprocess Execution (`util.py`, `workspace.py`)

**Current**: Two separate subprocess paths exist:
- `util.run_command()`: used by PO node for verification/acceptance. Takes explicit `cmd`, `cwd`, `timeout`, writes artifacts.
- `workspace._git()`: used internally for all git operations. Separate `subprocess.run()` call, 30s hardcoded timeout, no artifact logging.

**Conceptual seam**: `_git()` could accept an optional runner callable. For `util.run_command()`, similarly accept an optional subprocess runner. This allows tests to intercept all subprocess calls without monkeypatching `subprocess.run`.

**Behavior preservation**: Default runner is `subprocess.run`; no change to any call site.

### 4.3 Filesystem Access (`nodes_se.py`, `nodes_tr.py`, `nodes_po.py`)

**Current**: Nodes call `os.path.isfile()`, `open()`, `os.makedirs()`, `os.path.join()`, `os.path.exists()` directly. `_atomic_write` in `nodes_tr.py` uses `tempfile.mkstemp` + `os.fdopen` + `os.fsync` + `os.replace`.

**Conceptual seam**: The filesystem operations are tightly coupled to correctness (atomic writes, fsync). Abstracting them would add complexity with little benefit. Instead, the recommended seam is at the **node boundary**: test nodes by providing controlled `state` dicts pointing to temporary directories, rather than mocking individual `os` calls.

**Behavior preservation**: No code change; this is a testing strategy observation.

### 4.4 Environment Variable Read (`llm.py`)

**Current**: `os.environ.get("OPENAI_API_KEY")` at `llm.py` line 15, inside `_get_client()`.

**Conceptual seam**: Accept `api_key` as an optional parameter to `_get_client()` (and transitively to `complete()`), falling back to `os.environ.get(...)` if not provided. This allows tests to supply a key without setting env vars.

**Behavior preservation**: When no `api_key` is passed, behavior is identical to current code.

### 4.5 Time/Monotonic Clock (`util.py`)

**Current**: `time.monotonic()` called at `util.py` lines 99 and 108 for duration measurement.

**Conceptual seam**: This is low-priority. Duration is recorded but not used for control flow decisions. No seam needed for correctness testing.

### 4.6 `_get_verify_commands` Filesystem Probe (`nodes_po.py`)

**Current**: `os.path.isfile(os.path.join(repo_root, "scripts", "verify.sh"))` at line 23 determines which verification commands to run.

**Conceptual seam**: Extract the probe into a parameter or make the function accept a boolean `has_verify_script` flag. Alternatively, tests can simply create or omit `scripts/verify.sh` in a temp repo.

**Behavior preservation**: Default behavior probes the filesystem exactly as today.

---

## 5. Configuration & Global State Audit (Factory Only)

### Current Configuration Flow

```
CLI args (argparse in __main__.py)
  │
  ▼
run_cli(args) in run.py
  │
  ├─ args.repo → os.path.realpath() → repo_root
  ├─ args.work_order → os.path.realpath() → work_order_path → load_work_order() → WorkOrder
  ├─ args.out → os.path.realpath() → out_dir
  ├─ args.max_attempts → int
  ├─ args.llm_model → str
  ├─ args.llm_temperature → float
  ├─ args.timeout_seconds → int
  │
  ▼
initial_state dict (21 keys, passed to graph.invoke())
  │
  ▼
Each node unpacks what it needs from state dict
```

**Precedence**: There is only one precedence level—CLI arguments. Defaults are defined in `__main__.py` argparse definitions. No config files, no environment variable overrides (except `OPENAI_API_KEY` which is not a "configuration" but a credential).

### Module-Level Constants (not configuration, but worth cataloguing)

| Constant | File | Value | Used by |
|---|---|---|---|
| `MAX_CONTEXT_BYTES` | `nodes_se.py:11` | `200 * 1024` | `_read_context_files` |
| `MAX_FILE_WRITE_BYTES` | `schemas.py:78` | `200 * 1024` | `WriteProposal._check_size_limits` |
| `MAX_TOTAL_WRITE_BYTES` | `schemas.py:79` | `500 * 1024` | `WriteProposal._check_size_limits` |
| `MAX_EXCERPT_CHARS` | `util.py:54` | `2000` | `truncate()` |
| `ALLOWED_STAGES` | `schemas.py:125` | frozenset of 8 strings | `FailureBrief._validate_stage` |
| `GIT_TIMEOUT_SECONDS` | `workspace.py:11` | `30` | `_git()` default timeout — **added in Phase 1** |
| `ARTIFACT_*` (9 constants) | `util.py:178–188` | string filenames | Canonical artifact filenames — **added in Phase 2** |

These are all module-level `int`, `frozenset`, or `str` constants—no mutation, no global state.

### Global State

**None.** There are no module-level mutable variables, no singletons, no registries, no caches. The `openai.OpenAI()` client is instantiated fresh on every `complete()` call (no client reuse). State flows exclusively through the LangGraph state dict.

### Implicit Configuration Reads

| What | Where | Implicit? |
|---|---|---|
| `OPENAI_API_KEY` | `llm.py:_get_client()` line 15 | Yes — read from `os.environ` deep inside the call chain, not surfaced in `initial_state` |
| Git timeout (30s) | `workspace.py:GIT_TIMEOUT_SECONDS` | ~~Yes — hardcoded default~~ Now a named constant (Phase 1); still not configurable via CLI |
| LLM HTTP timeout | `llm.py:_get_client()` default 120s, overridden by `state["timeout_seconds"]` via `complete()` | Partially implicit — the 120s default is in `_get_client` signature |

### Proposed Normalization

**5.1: Surface `OPENAI_API_KEY` validation to `run_cli()`**

Currently, the missing-key error surfaces only when the first SE node runs (potentially after preflight, run_id computation, and artifact directory creation). Moving the check to `run_cli()` (alongside other preflight checks) would fail faster. Behavior change: the error message and timing of the `RuntimeError` would shift. To preserve behavior strictly, this would need to raise the same `RuntimeError` with the same message, but from a different call site. **This is low-risk but not zero-risk; flag for post-test-coverage implementation.**

**5.2: Make git timeout configurable (or at least a named constant)** — **DONE (Phase 1)**

~~The 30s timeout in `workspace._git()` is hardcoded.~~ Extracted to `GIT_TIMEOUT_SECONDS: int = 30` at module level in `workspace.py`. The constant is used as the default for the `timeout` parameter of `_git()`. Value and behavior are identical.

**5.3: No change to configuration precedence**

The current single-level precedence (CLI args only) is clean and correct. Do not introduce config files or env-var overrides.

---

## 6. Cleanup Execution Order (Minimal-Risk Plan)

Each step is independent of subsequent steps unless noted. Validate each step with a real `python -m factory run` invocation against a test repo (or by diffing artifacts).

### Step 1: Normalize ad-hoc truncation in `nodes_se.py`

- **Change**: Replace `raw[:500]` at line 199 with `truncate(raw, max_chars=500)`.
- **Risk**: Minimal. The only difference is the appended `\n...[truncated]` marker when the string exceeds 500 chars. This changes the `primary_error_excerpt` content in a `FailureBrief` artifact, which is fed back to the LLM on retry. The marker is informational and unlikely to alter LLM behavior.
- **Validate**: Trigger an `llm_output_invalid` failure and diff the `failure_brief.json`.

### Step 2: Extract combined-error-excerpt helper in `nodes_po.py`

- **Change**: Add private function `_combined_excerpt(cr)`, call it from both verify and acceptance failure blocks.
- **Risk**: Zero. Pure mechanical extraction; output is byte-identical.
- **Validate**: Trigger verify and acceptance failures, diff artifacts.

### Step 3: Extract TR failure-return helper in `nodes_tr.py`

- **Change**: Add private function `_tr_fail(...)`, replace 5 inline failure blocks.
- **Risk**: Zero. Same dict shapes, same artifact content.
- **Validate**: Trigger each TR failure stage (`write_scope_violation`, `stale_context`, `write_failed`), diff `write_result.json` and `failure_brief.json`.

### Step 4: Centralize artifact path constants

- **Change**: Add an `artifact_paths` section in `util.py` (or a private module) with functions like `attempt_dir(out_dir, run_id, idx)` and constants for filenames.
- **Risk**: Low. String construction only. If a typo is introduced, artifacts land in wrong paths—validate carefully.
- **Validate**: Full run, diff entire artifact tree.

### Step 5: Remove duplicate `failure_brief.json` write from `nodes_se.py`

- **Change**: Remove the `save_json(fb.model_dump(), ...)` calls at lines 188 and 211 in `nodes_se.py`. The `_finalize_node` in `graph.py` already writes this artifact.
- **Risk**: Low. Requires verifying that `_finalize_node` always executes after SE failure (it does—routing sends SE failures to finalize).
- **Validate**: Trigger SE failures (LLM exception, parse error), confirm `failure_brief.json` still appears in attempt dir.
- **Prerequisite**: Confidence in routing (verify `_route_after_se` sends to finalize when `failure_brief` is set).

### Step 6: Reconcile `RunSummary` schema with actual usage

- **Change**: Either (a) extend `RunSummary` to include `config: dict`, `error: Optional[str]`, `error_traceback: Optional[str]` and use it in `run.py`, or (b) remove `RunSummary` from `schemas.py` since it is unused. Option (a) is safer—it makes the schema match reality.
- **Risk**: Low if using option (a). The JSON output does not change.
- **Validate**: Diff `run_summary.json` before and after.

### Step 7: Extract `GIT_TIMEOUT_SECONDS` constant in `workspace.py`

- **Change**: Replace hardcoded `30` at line 11 with a module-level constant.
- **Risk**: Zero. Value is identical.
- **Validate**: Any run that performs git operations.

### Step 8 (optional, post-test-coverage): Add injection seams for LLM and subprocess

- **Change**: Add optional callable parameters to `complete()` and `_git()`.
- **Risk**: Low but touches function signatures. Should only be done after unit tests exist to verify no regressions.
- **Validate**: Existing behavior with default parameters must be identical.

---

## 7. What NOT to Clean Up (Important)

### 7.1 `_finalize_node` location in `graph.py`

The finalize node lives in `graph.py` alongside routing logic. It is tempting to move it to its own file (e.g., `nodes_finalize.py`). **Do not move it.** The finalize node is tightly coupled to:
- `FactoryState` TypedDict (same file)
- Routing functions that inspect the same state keys
- `rollback()` and `get_tree_hash()` imports

Moving it would create a circular import risk or require restructuring imports. The coupling is semantically correct: finalize is part of the graph's control flow, not a domain node.

### 7.2 Broad `except Exception` in `run.py:run_cli()` (line 106)

This catch-all is the outermost safety net. It handles:
- Unexpected LangGraph errors
- `RuntimeError` from git/LLM
- `KeyError` from malformed state
- Any other unexpected failure

Replacing it with specific exception types would risk letting some exceptions escape, causing the process to crash without rollback or summary. **Do not narrow this until a comprehensive exception taxonomy exists and is tested.**

### 7.3 The inner `except Exception` at `run.py` line 141

This block prints `f"CRITICAL: Failed to write run summary: {exc}"` where `exc` is the **outer** exception, not the inner one (the inner `except` has no `as` binding). This is almost certainly a latent bug (should print the inner exception), but fixing it changes stderr output, which is observable behavior. **Flag for fix only after tests capture the current and desired behavior.**

### 7.4 `_get_client()` instantiating a new client per call

In `llm.py`, `complete()` calls `_get_client()` on every invocation, creating a new `openai.OpenAI()` instance each time. Caching the client (e.g., as a module-level variable) would improve performance but introduce global mutable state—exactly the kind of entropy this cleanup aims to reduce. **Do not cache.**

### 7.5 `workspace._git()` separate from `util.run_command()`

These two subprocess paths serve different purposes:
- `_git()`: Internal git operations, no artifact logging needed, fixed 30s timeout.
- `run_command()`: User-facing verification/acceptance commands, full artifact logging, configurable timeout.

Merging them would force git operations to produce artifact files or force verification commands to lose artifact logging. **Do not merge.** The separation is intentional.

### 7.6 `FactoryState` TypedDict with `total=False`

All fields are optional in the TypedDict, and no node actually uses it for type checking (all accept `dict`). It is tempting to enforce stricter typing. **Do not change.** LangGraph's `StateGraph` uses the TypedDict for channel definitions. Changing `total=False` or adding `Required[]` annotations could alter LangGraph's channel merge behavior in subtle ways.

### 7.7 Deferred import of `Counter` in `nodes_tr.py`

Line 68: `from collections import Counter` inside the function body. This is unusual but intentional—it only imports when a duplicate-path condition is detected (a rare error path). Moving it to the top level is cosmetically cleaner but provides no behavioral benefit. **Low priority, no risk, but also no value.**

---

## 8. Readiness Checklist for Unit Test Generation (Factory Only)

The following criteria define when `./factory` is ready for comprehensive unit test coverage. Each criterion maps to a cleanup step from the Advisory §5 revised execution order.

| # | Criterion | Current Status | Blocking Cleanup Step |
|---|---|---|---|
| 1 | No duplicate artifact writes for same logical output | `failure_brief.json` written in 2 places (reclassified as write-ahead resilience — see Advisory §1B) | N/A (intentional) |
| 2 | Artifact path construction centralized | **DONE (Phase 2)** — `make_attempt_dir()` + 9 `ARTIFACT_*` constants in `util.py` | ~~Phase 2, Step 5~~ Complete |
| 3 | TR failure paths extracted to single helper | **DONE (Phase 1)** — `_tr_fail()` in `nodes_tr.py` | ~~Step 3~~ Complete |
| 4 | PO error excerpt construction extracted | **DONE (Phase 1)** — `_combined_excerpt()` in `nodes_po.py` | ~~Step 2~~ Complete |
| 5 | Truncation behavior consistent | `raw[:500]` is intentional sub-slice (see Advisory §1A) — defer normalization | Phase 3, Step 7 |
| 6 | `RunSummary` schema removed (dead weight) | **DONE (Phase 1)** — removed from `schemas.py` | ~~Step 4~~ Complete |
| 7 | No hidden side effects on import | Already satisfied | None |
| 8 | Module-level constants are all immutable and named | **DONE (Phase 1)** — `GIT_TIMEOUT_SECONDS` extracted in `workspace.py` | ~~Step 1~~ Complete |
| 9 | No global mutable state | Already satisfied | None |
| 10 | Test seams exist for LLM and subprocess | Not yet; `mock.patch` works but is fragile | Phase 2+ (post-coverage) |

**Minimum bar for test generation**: Phases 1 and 2 complete. Only Phase 3 step 7 (truncation normalization, deferred) remains. Test seams (step 8) can be added incrementally as tests are written.

**Import-time safety**: `./factory` has no module-level side effects. Importing any module does not trigger filesystem access, network calls, or subprocess execution. This is already test-ready.

---

## Advisory Notes / Risk Review

### 1) Strict behavioral preservation vs "probably fine"

#### A. Truncation normalization in `nodes_se.py`

**Verdict: NOT strict-preserve. My original recommendation was based on a misread.**

On closer inspection, `raw[:500]` at line 199 is not a competing truncation — it is an *intentional inline slice* nested **inside** an outer `truncate()` call. The actual code is:

```196:204:factory/nodes_se.py
        fb = FailureBrief(
            stage="llm_output_invalid",
            primary_error_excerpt=truncate(
                f"Parse error: {exc}\nRaw response (first 500 chars): {raw[:500]}"
            ),
            constraints_reminder=(
                "LLM must output valid JSON with keys 'summary' and 'writes'. "
                "Each write needs 'path', 'base_sha256', and 'content'."
            ),
```

The structure is:
1. Slice `raw` to 500 chars (no marker) — keeps the composed message compact
2. Compose: `"Parse error: {exc}\nRaw response (first 500 chars): {raw[:500]}"`
3. Pass the composed string through `truncate()` (adds `\n...[truncated]` if > 2000 chars)

If `raw[:500]` were replaced with `truncate(raw, max_chars=500)`, the result would embed `\n...[truncated]` *inside* the composed message (before the outer `truncate()` runs). Concrete differences:

- `failure_brief.json` changes: the `primary_error_excerpt` field gains 16 extra characters (`\n...[truncated]`) mid-string whenever the raw LLM response exceeds 500 chars. This changes every `failure_brief.json` artifact produced on a parse-failure path.
- Retry prompt dynamics change: `_build_prompt` in `nodes_se.py` feeds `failure_brief.primary_error_excerpt` back into the LLM prompt (lines 113–121). A different excerpt means a different prompt, which means potentially different LLM behavior on retry.

**Classification: pragmatic-preserve, not strict-preserve.**

**Safest alternative**: Do not change this at all. The `raw[:500]` is a deliberate sub-slice, not an inconsistency. If normalization is still desired, defer it until after tripwire tests exist that capture the exact `failure_brief.json` contents for an `llm_output_invalid` failure. Even then, the right fix is to give the inline slice its own named helper (e.g., `_clip(raw, 500)`) that preserves the no-marker semantics, rather than reusing `truncate()`.

---

#### B. Removing duplicate `failure_brief.json` writes

**Verdict: NOT safe to remove. The duplication is a deliberate write-ahead resilience feature, not entropy.**

Crash/kill analysis — consider what exists on disk if the process receives SIGKILL between SE return and `_finalize_node` execution:

| Scenario | Current behavior (SE writes) | Proposed behavior (SE doesn't write) |
|---|---|---|
| SIGKILL after SE LLM exception (line 188) | `failure_brief.json` exists | `failure_brief.json` **missing** |
| SIGKILL after SE parse failure (line 211) | `failure_brief.json` + `raw_llm_response.json` exist | Only `raw_llm_response.json` exists |
| OOM kill during graph routing | `failure_brief.json` exists | `failure_brief.json` **missing** |

The routing from SE to finalize is:

```53:57:factory/graph.py
def _route_after_se(state: dict) -> str:
    """After SE: proceed to TR if a proposal was produced, else finalize."""
    if state.get("failure_brief") is not None:
        return "finalize"
    return "tr"
```

LangGraph must execute the routing function, invoke `_finalize_node`, and that node must reach line 104 before the artifact is persisted. There is a real window where the process can die without `failure_brief.json` existing on disk.

**Reclassification**: This is not entropy. It is write-ahead artifact persistence — the same pattern used in databases (write-ahead log before commit). The SE node writes artifacts eagerly because it cannot assume finalize will run.

**Revised recommendation**: Keep both writes. To reduce confusion, add a brief inline comment at the SE write sites:

```python
# Write-ahead: persist now in case process is killed before finalize runs.
save_json(fb.model_dump(), os.path.join(attempt_dir, "failure_brief.json"))
```

The finalize overwrite (line 104 in `graph.py`) is harmless — `save_json` is a full replace, and the content is identical (`failure_brief` flows through state unchanged). Document this: "finalize always overwrites; SE writes are for crash resilience."

---

### 2) "Zero risk" refactors that may still change bytes

#### `nodes_po.py` excerpt construction

Claim: extracting the combined-error-excerpt helper is byte-identical. **Verified: correct.**

Current code (lines 66–70):

```python
combined = ""
if cr.stderr_trunc:
    combined += f"[stderr]\n{cr.stderr_trunc}\n"
if cr.stdout_trunc:
    combined += f"[stdout]\n{cr.stdout_trunc}\n"
# then: truncate(combined.strip())
```

Tracing through all three cases:

**Both present**: `"[stderr]\n{s}\n[stdout]\n{o}\n"` → `.strip()` → `"[stderr]\n{s}\n[stdout]\n{o}"`. A join-based helper producing `"\n".join([f"[stderr]\n{s}", f"[stdout]\n{o}"])` yields the identical string: `"[stderr]\n{s}\n[stdout]\n{o}"`.

**Only stderr**: `"[stderr]\n{s}\n"` → `.strip()` → `"[stderr]\n{s}"`. Join: `"[stderr]\n{s}"`. Identical.

**Only stdout**: `"[stdout]\n{o}\n"` → `.strip()` → `"[stdout]\n{o}"`. Join: `"[stdout]\n{o}"`. Identical.

**Neither present**: `""` → `.strip()` → `""`. Join of `[]` → `""`. Identical.

**Verification method**: Run a single failing verification command, diff `failure_brief.json` before/after extraction. One run covers it, but test both verify and acceptance paths (they use the same pattern but are separate call sites).

**One caution**: The proposed helper must NOT apply `.strip()` internally if the caller also strips — that would double-strip. The current code applies `.strip()` at the call site (`truncate(combined.strip())`). The helper should return the raw combined string, and the caller should continue to `.strip()` and `truncate()` it. Or the helper takes responsibility for both. Pick one, document it, verify once.

---

#### `nodes_tr.py` failure helper

Claim: extracting the failure-return helper is byte-identical. **Verified: correct, with one caveat.**

The `write_result.json` artifact is written via `save_json`, which uses `json.dump(data, fh, indent=2, sort_keys=True)`. Since `sort_keys=True` is set, the Python dict's insertion order is irrelevant — JSON output is alphabetically sorted. As long as the helper passes the same key-value pairs, the file is byte-identical.

The return dict goes to LangGraph state. LangGraph merges each key independently into its own channel. Dict key order has no effect on state behavior.

**Caveat**: The five failure blocks produce `write_result.json` dicts with identical key sets — `{"write_ok": ..., "touched_files": ..., "errors": [...]}` — but the `errors` list always contains exactly `[fb.primary_error_excerpt]`. If the helper constructs this list from the `FailureBrief` object, the value is identical. If it were accidentally constructed differently (e.g., `[excerpt]` where `excerpt` is the raw string before FailureBrief validation), there could be a subtle difference if the validator modifies the string. In practice, `FailureBrief` does not modify `primary_error_excerpt` — there is no validator on that field. So this is safe.

**Verification method**: Trigger each of the five failure paths (duplicate paths, out-of-scope, path escape, stale hash, write failure), diff `write_result.json` and `failure_brief.json` before/after. The duplicate-path and path-escape cases may be hardest to trigger in a real run; consider adding them to the tripwire test list.

---

### 3) Injection seams: fit to LangGraph architecture

**My original suggestion (optional parameters on node functions) does not work with LangGraph.**

Nodes are registered as:

```157:160:factory/graph.py
    graph.add_node("se", se_node)
    graph.add_node("tr", tr_node)
    graph.add_node("po", po_node)
    graph.add_node("finalize", _finalize_node)
```

LangGraph calls each node as `node_fn(state)` — one positional argument, no kwargs. Adding optional parameters to the function signature would be ignored by LangGraph's invocation.

**Three viable seam shapes, ranked by fit:**

1. **Node factories (closures)** — best fit. Define `make_se_node(complete_fn=llm.complete)` that returns a `def se_node(state) -> dict` closing over `complete_fn`. In `graph.py`, wire as `graph.add_node("se", make_se_node())`. Tests call `make_se_node(complete_fn=mock_fn)` to get a testable node function directly, bypassing the graph entirely. This is the minimum-change seam: each node file gains one wrapper function; the node's internal logic is unchanged; `graph.py` changes one line per node.

2. **State-based injection** — workable but pollutes state. Add keys like `_llm_complete_fn` to `initial_state`. Nodes read them via `state.get("_llm_complete_fn", llm.complete)`. Downsides: callables in state are not serializable, which breaks LangGraph checkpointing if ever used; the `FactoryState` TypedDict would need `Any`-typed keys; it violates the principle that state carries data, not behavior.

3. **Module-level patching** (`unittest.mock.patch`) — already works today, zero code changes. Sufficient for initial test coverage. Downsides: fragile against import-path refactors; couples tests to internal module structure; not usable outside of test frameworks.

**Recommendation**: Start with option 3 (no code changes, immediate testability). Graduate to option 1 (node factories) when the test suite matures and import-path coupling becomes a maintenance burden. Do not use option 2.

---

### 4) RunSummary: dead weight — remove it — **DONE (Phase 1)**

**Analysis**: `RunSummary` is never instantiated. The actual `run_summary.json` is written as a raw dict in two places:

- Normal path (`run.py` lines 158–167): keys are `{run_id, work_order_id, verdict, total_attempts, baseline_commit, repo_tree_hash_after, config, attempts}`. The schema lacks `config`.
- Error path (`run.py` lines 126–137): keys add `error` and `error_traceback`. The schema lacks all three extra keys.

**Making it a validated contract is dangerous**:
- Adding `model_validate()` at write time in the normal path is safe *if* the schema matches.
- Adding `model_validate()` in the error handler path (line 140) is **actively harmful**: if validation raises, the emergency summary is never written, turning a recoverable crash into an invisible one. The error handler must be infallible.
- Adding `schema_version` is premature — there are no consumers parsing this schema programmatically yet.

**Minimum-entropy choice: remove `RunSummary` from `schemas.py`.** *(Executed in Phase 1.)*

Justification:
- An unenforced schema is worse than no schema — it creates false confidence and drifts silently (which it already has).
- `AttemptRecord` and `CmdResult` (also in `schemas.py`) are similarly unenforced at the summary-writing boundary, but they are used as intermediate models by nodes (`cr.model_dump()` in PO, etc.), so they carry real value. `RunSummary` carries none.
- If a validated summary contract is needed later, it should be introduced alongside a consumer that depends on it, with explicit versioning and a test that round-trips through the schema.

**Risk**: Zero. Removing an unused class from `schemas.py` changes no behavior and no output.

---

### 5) Revised execution order (risk-minimizing)

Previous order had artifact-altering changes early. Revised order prioritizes provably byte-identical changes first and defers anything that touches retry dynamics.

| Phase | Step | Risk | Verification | Status |
|---|---|---|---|---|
| **Phase 1: Provably safe** | | | | |
| 1 | Extract `GIT_TIMEOUT_SECONDS` constant in `workspace.py` | Zero (value unchanged) | All imports pass; value asserted == 30 | **DONE** |
| 2 | Extract combined-error-excerpt helper in `nodes_po.py` | Zero (byte-identical, verified above) | Byte-equivalence asserted for all 4 edge cases | **DONE** |
| 3 | Extract TR failure-return helper in `nodes_tr.py` | Zero (byte-identical, verified above) | All imports pass; `save_json` sort_keys=True makes dict order irrelevant | **DONE** |
| 4 | Remove `RunSummary` from `schemas.py` | Zero (unused class) | `grep -r RunSummary factory/` confirms no remaining references | **DONE** |
| **Phase 2: Low risk, needs diff verification** | | | | |
| 5 | Centralize artifact path constants in `util.py` | Low (string construction only) | All imports pass; grep confirms no stale string literals remain | **DONE** |
| 6 | Add write-ahead comments to SE's `failure_brief.json` writes | Zero (comment only) | N/A | **DONE** |
| **Phase 3: Defer until tripwire tests exist** | | | | |
| 7 | Truncation normalization in `nodes_se.py` | **Pragmatic-preserve only** — changes `failure_brief.json` content and retry prompt | Requires tripwire test that captures exact `failure_brief.json` for `llm_output_invalid` | Blocked |
| ~~8~~ | ~~Remove duplicate `failure_brief.json` writes~~ | **Reclassified: do not execute** — write-ahead resilience, not entropy | N/A | Cancelled |

**What "tripwire tests" means here**: A minimal test that invokes a node (or the full graph with a mock LLM) for a known-failure scenario, captures the artifact files, and asserts their content byte-for-byte. Once these exist, Phase 3 changes can be made and immediately validated against the captured baseline.

~~**Phase 1 can be executed in a single commit with high confidence.**~~ **Phase 1 is complete.** ~~Phase 2 requires one real run to diff-verify.~~ **Phase 2 is complete.** Phase 3 should not be attempted until tests cover the affected failure paths.