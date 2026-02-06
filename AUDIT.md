# AUDIT.md — Comprehensive Audit of `./factory/` LangGraph Execution Loop

**Auditor**: Senior engineer, read-only audit  
**Date**: 2026-02-06  
**Scope**: All modules under `./factory/` (11 files)  
**Commit basis**: branch `new_attempt2`

---

## 1) Executive Summary

**What the loop does:**
- Implements a deterministic SE → TR → PO retry loop via LangGraph: the SE (LLM) proposes full-file writes as JSON; the TR validates scope and precondition hashes then performs atomic writes; the PO runs global verification and acceptance commands. On failure, the harness rolls back via `git reset --hard` + `git clean -fd` and retries.

**What it does well:**
- Clean separation of concerns: each stage (SE/TR/PO) is a distinct LangGraph node with explicit routing.
- Scope enforcement: `touched_files ⊆ allowed_files` is computed from the proposal, not from LLM claims (`nodes_tr.py:61-85`).
- Stale-context protection: `base_sha256` checks are performed for ALL files BEFORE any writes are applied (`nodes_tr.py:111-136`).
- No `shell=True` anywhere; `subprocess.run` always uses `shell=False` (`util.py:106`, `workspace.py:13-19`).
- Atomic file writes via temp-file + fsync + rename (`nodes_tr.py:23-40`).
- Bounded error excerpts (2000 chars) prevent unbounded LLM context from failure feedback (`util.py:54-61`).
- Full stdout/stderr of every command is persisted to disk; only truncated excerpts enter state (`util.py:88-143`).
- Deterministic `run_id` derived from canonical JSON of work order + baseline commit (`util.py:41-47`).
- Pydantic models enforce schema constraints at parse time (write size limits, path validation, stage enum).

**Top risks (ordered by severity):**
1. **CRITICAL — No top-level exception guard around `graph.invoke()`**: an unhandled exception in any node (e.g., rollback failure, `get_tree_hash` crash) leaves the repo dirty with no `run_summary.json` written (`run.py:82`).
2. **HIGH — Outdir-inside-repo not validated**: if `--out` resolves inside the repo, artifacts are written to the repo tree; `git clean -fd` on rollback destroys them, and `git add -A` on PASS stages them (`run.py:18`; no check).
3. **HIGH — `git clean -fd` respects `.gitignore`**: newly-created files matching `.gitignore` patterns survive rollback, leaking changes (`workspace.py:81`).
4. **HIGH — `get_tree_hash` side-effects on PASS**: `git add -A` stages ALL files (including verification artifacts like `__pycache__`, `.pytest_cache`), polluting the tree hash and leaving the index dirty (`workspace.py:52-65`).
5. **MEDIUM — Prompt not persisted**: the SE prompt is constructed in memory and never saved to disk, making post-mortem reconstruction incomplete (`nodes_se.py:167`).
6. **MEDIUM — Raw LLM response only saved on parse failure**: on successful parse, the raw response is discarded; only `proposed_writes.json` is saved (`nodes_se.py:200-211`).
7. **MEDIUM — LLM call has no timeout**: `llm.complete()` can hang indefinitely; no timeout is set on the OpenAI client (`llm.py:30-34`).
8. **MEDIUM — `shlex.split` exception in PO not caught**: a malformed acceptance command string crashes the PO node with no `FailureBrief` (`nodes_po.py:88`).
9. **LOW — `forbidden` list is advisory only**: the harness never enforces forbidden constraints; only the prompt mentions them (`nodes_se.py:81-85`; no TR check).
10. **LOW — Duplicate write paths in proposals not rejected**: two writes to the same path both pass hash checks (all checks run before any writes), and the second silently overwrites the first (`nodes_tr.py:111-161`).

**Biggest missing capabilities:**
- No global run timeout (only per-command).
- No mechanism to abort/cancel a running harness gracefully.
- No `example_work_order.json` shipped (spec requires it; file is missing).

---

## 2) Architecture Map

### Module → Responsibility Table

| Module | Responsibility | Key Functions/Classes | Evidence |
|--------|---------------|----------------------|----------|
| `__main__.py` | CLI entrypoint, argparse wiring | `main()` | Lines 9–63; delegates to `run.run_cli(args)` |
| `run.py` | Orchestration: preflight, graph invocation, run_summary | `run_cli(args)` | Lines 14–107; loads work order, checks git, builds graph, writes summary |
| `graph.py` | LangGraph definition, routing, finalize node | `FactoryState`, `build_graph()`, `_finalize_node()`, `_route_after_*()` | Lines 23–173; StateGraph with 4 nodes + 3 conditional edges |
| `nodes_se.py` | SE prompt construction, LLM call, parse WriteProposal | `se_node()`, `_build_prompt()`, `_read_context_files()` | Lines 1–213 |
| `nodes_tr.py` | Scope check, hash check, atomic file writes | `tr_node()`, `_atomic_write()` | Lines 1–172 |
| `nodes_po.py` | Global verification + acceptance commands | `po_node()`, `_get_verify_commands()` | Lines 1–125 |
| `schemas.py` | Pydantic models, path validation, load helpers | `WorkOrder`, `FileWrite`, `WriteProposal`, `FailureBrief`, `CmdResult`, `AttemptRecord`, `RunSummary`, `load_work_order()` | Lines 1–195 |
| `util.py` | Hashing, truncation, JSON IO, command runner, path helpers | `sha256_bytes()`, `sha256_file()`, `compute_run_id()`, `truncate()`, `save_json()`, `run_command()`, `split_command()`, `normalize_path()`, `is_path_inside_repo()` | Lines 1–170 |
| `workspace.py` | Git queries and mutations | `is_git_repo()`, `is_clean()`, `get_baseline_commit()`, `get_tree_hash()`, `rollback()` | Lines 1–86 |
| `llm.py` | OpenAI Chat Completions wrapper, JSON parsing | `complete()`, `parse_proposal_json()`, `_get_client()` | Lines 1–50 |
| `__init__.py` | Package marker | (empty) | 1 line comment |

### Execution Flow Diagram

```
 ┌─────────────────────────────────────────────────────┐
 │                    __main__.py                       │
 │              argparse → run_cli(args)                │
 └──────────────────────┬──────────────────────────────┘
                        │
 ┌──────────────────────▼──────────────────────────────┐
 │                     run.py                           │
 │  1. load_work_order(path)                            │
 │  2. is_git_repo(repo) + is_clean(repo)               │
 │  3. get_baseline_commit(repo)                        │
 │  4. compute_run_id(wo, baseline)                     │
 │  5. build_graph() → graph.invoke(initial_state)      │
 │  6. save run_summary.json                            │
 └──────────────────────┬──────────────────────────────┘
                        │
         ┌──────────────▼──────────────┐
         │       LangGraph Loop        │
         │                             │
         │  ┌─────┐   proposal ok?     │
   ┌────►│  │ SE  ├──────────────┐     │
   │     │  └─────┘    yes       │no   │
   │     │       ┌─────▼─────┐   │     │
   │     │       │    TR     │   │     │
   │     │       └─────┬─────┘   │     │
   │     │     write_ok?│    no  │     │
   │     │       ┌─────▼─────┐   │     │
   │     │  yes  │    PO     │   │     │
   │     │       └─────┬─────┘   │     │
   │     │             │         │     │
   │     │       ┌─────▼─────────▼──┐  │
   │     │       │    FINALIZE      │  │
   │     │       │ • record attempt │  │
   │     │       │ • rollback/hash  │  │
   │     │       │ • route decision │  │
   │     │       └───────┬──────────┘  │
   │     │    PASS or    │  FAIL &     │
   │     │    exhausted  │  retries    │
   │     │       ▼       │  remain     │
   │     │      END      │             │
   │     └───────────────┘             │
   └───────────────────────────────────┘
```

**Routing logic (graph.py:53-73):**
- After SE: if `failure_brief` is set → finalize; else → TR.
- After TR: if `failure_brief` is set → finalize; else → PO.
- After PO: always → finalize (unconditional edge, line 168).
- After finalize: if `verdict == "PASS"` → END; if `attempt_index > max_attempts` → END; else → SE.

---

## 3) Execution Loop Analysis

### Step-by-Step Narrative

**Preflight (run.py:22-48):**
1. `load_work_order(path)` — Pydantic validates all fields: path safety, `context_files ⊆ allowed_files`, `acceptance_commands` non-empty, max 10 context files.
2. `is_git_repo(repo_root)` — runs `git rev-parse --is-inside-work-tree` with `shell=False`.
3. `is_clean(repo_root)` — runs `git status --porcelain`; any output = not clean.
4. `get_baseline_commit(repo_root)` — runs `git rev-parse HEAD`.
5. `compute_run_id(work_order_dict, baseline_commit)` — deterministic SHA-256 of canonical JSON + baseline, truncated to 16 hex chars.

**Per-attempt (SE → TR → PO → finalize):**

**SE node (nodes_se.py:147-213):**
1. Re-reads context files from disk (bounded at 200 KB total; `_read_context_files` at line 20-53).
2. Computes SHA-256 of each context file (used as `base_sha256` hint for LLM).
3. Constructs prompt with: work-order details, allowed/forbidden lists, context file contents+hashes, prior `FailureBrief` (if any), strict JSON output format instructions.
4. Calls `llm.complete(prompt, model, temperature)`.
5. Parses response: strips markdown fences (`llm.parse_proposal_json`), then validates via `WriteProposal(**parsed)`.
6. Saves `proposed_writes.json` artifact.
7. On LLM exception: creates `FailureBrief(stage="exception")`, saves it, returns.
8. On parse failure: creates `FailureBrief(stage="llm_output_invalid")`, saves raw response + failure brief, returns.

**TR node (nodes_tr.py:48-172):**
1. Computes `touched_files` = deduplicated, normalized paths from `proposal.writes`.
2. Scope check: all touched_files must be in `allowed_files`. Violation → `FailureBrief(stage="write_scope_violation")`.
3. Path-safety check: all paths must resolve inside repo via `is_path_inside_repo()`. Violation → `FailureBrief(stage="write_scope_violation")`.
4. Base-hash check (ALL files checked BEFORE any writes): for each write, compute current file SHA-256 and compare to `base_sha256`. Mismatch → `FailureBrief(stage="stale_context")`.
5. Apply writes: for each write, `_atomic_write(abs_path, content)` — creates temp file, writes, fsyncs, renames.
6. On any write failure: `FailureBrief(stage="write_failed")`.
7. Saves `write_result.json`.

**PO node (nodes_po.py:37-125):**
1. Determines verify commands: if `scripts/verify.sh` exists → `["bash", "scripts/verify.sh"]`; otherwise fallback: `compileall`, `pip --version`, `pytest`.
2. Runs each verify command via `run_command()` (subprocess, no shell, per-command timeout, full stdout/stderr to files).
3. Any nonzero → `FailureBrief(stage="verify_failed")`, saves `verify_result.json`, returns.
4. Runs acceptance commands (split via `shlex.split`), same execution pattern.
5. Any nonzero → `FailureBrief(stage="acceptance_failed")`, saves `acceptance_result.json`, returns.
6. All pass → `failure_brief: None`.

**Finalize node (graph.py:81-143):**
1. Reads `failure_brief` from state.
2. Determines verdict: `"FAIL"` if `failure_brief` else `"PASS"`.
3. On FAIL: calls `rollback(repo_root, baseline)` (`git reset --hard` + `git clean -fd`).
4. On PASS: calls `get_tree_hash(repo_root)` (`git add -A` + `git write-tree`).
5. Builds `attempt_record` dict, appends to `attempts` list.
6. Increments `attempt_index`, resets per-attempt fields (proposal, touched_files, write_ok, verify/acceptance results).
7. Preserves `failure_brief` in state (so SE can read it on retry).

### Termination Conditions

1. **PASS**: PO returns `failure_brief: None` → finalize sets `verdict="PASS"` → `_route_after_finalize` returns `END`.
2. **Exhausted attempts**: `attempt_index > max_attempts` → `_route_after_finalize` returns `END`.
3. **No infinite loop possible**: `attempt_index` is strictly incremented by 1 on every finalize invocation (graph.py:131). Since `max_attempts` is finite, `attempt_index` will eventually exceed it. **Sound.**

### Edge case: `max_attempts=0`
- Initial `attempt_index=1`. SE runs. After finalize, `attempt_index=2`. `2 > 0` → END.
- Result: 1 attempt executes despite `max_attempts=0`. No validation prevents this (`__main__.py` does not enforce `max_attempts >= 1`).

### Determinism and Ordering

- `run_id` is deterministic given identical work order + baseline commit.
- Context files are read in `sorted()` order (nodes_se.py:23).
- `touched_files` are `sorted()` (nodes_tr.py:61).
- `allowed_files` in the prompt are `sorted()` (nodes_se.py:77).
- Verify commands are executed in a fixed order (nodes_po.py:55-78).
- Acceptance commands are executed in work-order order (nodes_po.py:87-114).
- **Non-deterministic element**: the LLM response itself (even at temperature 0, responses may vary across runs due to infrastructure-level non-determinism).

### State Persisted Across Iterations

| Key | Persisted across retries? | How |
|-----|--------------------------|-----|
| `attempts` | Yes | List accumulates attempt records (graph.py:118-119) |
| `failure_brief` | Yes (on FAIL) | Preserved for SE retry prompt (graph.py:137) |
| `attempt_index` | Yes | Incremented each finalize (graph.py:131) |
| `verdict` | Yes | Set each finalize; checked by routing |
| `proposal`, `touched_files`, `write_ok`, `verify_results`, `acceptance_results` | Reset | Cleared each finalize (graph.py:133-139) |

---

## 4) Repo Safety & Rollback Analysis

### How Changes Are Applied

1. **In-situ direct writes**: `_atomic_write()` (nodes_tr.py:23-40) writes to a temp file in the same directory as the target, fsyncs, then atomically replaces via `os.replace()`.
2. **Parent directory creation**: `os.makedirs(parent, exist_ok=True)` (nodes_tr.py:26). New directories can be created inside the repo.
3. **No workspace copy**: all writes happen directly in the product repo (by design per AGENTS.md §2.3).

### Rollback Mechanism

- `rollback()` in `workspace.py:73-86`:
  1. `git reset --hard <baseline_commit>` — restores all tracked files to baseline.
  2. `git clean -fd` — removes untracked files and directories.
- Triggered in `_finalize_node` whenever `verdict == "FAIL"` (graph.py:123-124).

### Guarantee Analysis

| Dirty-state vector | Prevention/Recovery | Status | Evidence |
|--------------------|--------------------|--------|----------|
| Modified tracked files | `git reset --hard` restores them | **GUARANTEED** | workspace.py:75 |
| New untracked files | `git clean -fd` removes them | **MOSTLY GUARANTEED** | workspace.py:81 |
| New untracked files matching `.gitignore` | `git clean -fd` (no `-x` flag) respects gitignore | **GAP — files survive rollback** | workspace.py:81; `-fd` not `-fdx` |
| Deleted tracked files | `git reset --hard` restores them | **GUARANTEED** | workspace.py:75 |
| New directories created by `_atomic_write` | `git clean -fd` removes untracked dirs | **MOSTLY GUARANTEED** (same gitignore gap) | nodes_tr.py:26 |
| Permission changes | Not addressed | **GAP** — git does not restore Unix permissions beyond the executable bit | No code addresses this |
| Verification artifacts (`__pycache__`, `.pytest_cache`) | On FAIL: cleaned by `git clean -fd` (if not gitignored). On PASS: staged by `git add -A` | **GAP on PASS** — artifacts pollute index/tree hash | workspace.py:54 stages all; typically `__pycache__` is gitignored so unaffected, but `.pytest_cache` may not be |
| Artifacts in outdir inside repo | On FAIL: `git clean -fd` destroys them. On PASS: `git add -A` stages them | **CRITICAL GAP** — outdir inside repo is not rejected | run.py:18; no validation |
| Process crash between write and rollback | Repo left dirty; no recovery mechanism | **INHERENT RISK** of in-situ design | No crash handler exists |
| Rollback failure (git command fails) | `rollback()` raises `RuntimeError`; NOT caught by finalize or `run_cli` | **CRITICAL** — repo left dirty, no summary written | workspace.py:77-86; graph.py:124; run.py:82 |
| `get_tree_hash` failure on PASS | `git add -A` or `git write-tree` fails; exception propagates; changes remain applied + partially staged | **HIGH** — no rollback attempted since verdict was PASS | graph.py:127; workspace.py:52-65 |

### Critical Edge Cases

#### 1. Outdir inside repo (UNMITIGATED)
**Scenario**: User passes `--out ./output` where `./output` is inside the product repo.  
**On FAIL**: `git clean -fd` removes the entire `output/` directory including all artifacts from the current and prior attempts.  
**On PASS**: `git add -A` stages all artifact files; tree hash includes them.  
**Evidence**: `run.py:18` does `os.path.realpath(args.out)` but never checks `out_dir` vs `repo_root`.  
**Risk rating**: **CRITICAL**. Data loss (artifacts destroyed on rollback) + tree hash pollution.

#### 2. Gitignored file writes survive rollback (UNMITIGATED)
**Scenario**: `allowed_files` includes a path like `.env` or `build/output.js` that matches a `.gitignore` pattern. LLM writes to it. PO fails.  
**Result**: `git reset --hard` doesn't touch the file (it's not tracked). `git clean -fd` doesn't touch it (it's gitignored).  
**Evidence**: `workspace.py:81` uses `git clean -fd` not `-fdx`.  
**Risk rating**: **HIGH**. Violated files persist silently.

#### 3. Process crash mid-write (INHERENT)
**Scenario**: Process killed (SIGKILL, OOM) after first of N writes applied but before rollback.  
**Result**: Repo has partial writes applied; no automatic recovery.  
**Mitigation**: The pre-flight clean-tree check on next run will detect the dirty state and refuse to proceed. But manual cleanup is required.  
**Risk rating**: **MEDIUM** (mitigated by preflight on next run).

#### 4. Unhandled exception in any node (UNMITIGATED)
**Scenario**: `shlex.split` raises `ValueError` in PO (malformed acceptance command); `rollback()` raises `RuntimeError` in finalize; OpenAI SDK raises unexpected exception type.  
**Result**: Exception propagates through LangGraph → `run_cli` → process crashes. No `run_summary.json`. If writes were applied, repo is dirty.  
**Evidence**: `run.py:82` has no try/except around `graph.invoke()`. `nodes_po.py:88` does not catch `shlex.split` exceptions.  
**Risk rating**: **HIGH**.

#### 5. `get_tree_hash` stages non-proposal files (UNMITIGATED on PASS)
**Scenario**: Verification/acceptance commands create files (e.g., `__pycache__`, `.pytest_cache`, coverage reports). On PASS, `get_tree_hash` runs `git add -A` which stages EVERYTHING.  
**Result**: Tree hash includes verification artifacts. Index is dirty (staged but not committed).  
**Evidence**: `workspace.py:54` runs `git add -A` unconditionally.  
**Risk rating**: **MEDIUM**. Tree hash is non-deterministic across environments.

### Evidence Preserved for Postmortems

- `write_result.json` per attempt (write_ok, touched_files, errors).
- `verify_result.json` per attempt (all CmdResult entries up to first failure).
- `acceptance_result.json` per attempt (same).
- Full stdout/stderr files for every command (`verify_N_stdout.txt`, etc.).
- `proposed_writes.json` (the parsed WriteProposal, not the raw LLM output on success).
- `failure_brief.json` on failure.
- `run_summary.json` at end (if process doesn't crash).
- `raw_llm_response.json` ONLY on parse failure (not on success).

---

## 5) Error Propagation to SE

### What SE Receives on Failure

The SE prompt includes a `FailureBrief` section (nodes_se.py:113-122) with:
- `stage` (e.g., `verify_failed`, `acceptance_failed`, `stale_context`)
- `command` (the failing command string, if applicable)
- `exit_code`
- `primary_error_excerpt` (truncated to 2000 chars)
- `constraints_reminder` (a static string per stage)

### What Information Is Collected Per Stage

| Stage | Command captured? | Exit code? | Stderr/stdout? | Evidence |
|-------|------------------|------------|----------------|----------|
| `verify_failed` | Yes (`" ".join(cmd)`) | Yes | Yes (stderr preferred, falls back to stdout; truncated to 2000 chars) | nodes_po.py:66-72 |
| `acceptance_failed` | Yes (original cmd string) | Yes | Yes (same pattern) | nodes_po.py:99-104 |
| `write_scope_violation` | No | No | N/A — excerpt lists violating files | nodes_tr.py:69-72 |
| `stale_context` | No | No | N/A — excerpt shows expected vs actual hash | nodes_tr.py:116-122 |
| `write_failed` | No | No | Exception message in excerpt | nodes_tr.py:147-149 |
| `llm_output_invalid` | No | No | Parse error + first 500 chars of raw response | nodes_se.py:190-193 |
| `exception` | No | No | Exception string | nodes_se.py:177-179 |

### Analysis of Feedback Quality

**Strengths:**
- The `FailureBrief` is structured and typed, ensuring consistent delivery.
- Truncation (2000 chars) prevents unbounded context but preserves the first portion of error output.
- The SE prompt re-reads context files on each retry (nodes_se.py:164), so the LLM sees the CURRENT state of files after rollback (which is the baseline state).

**Weaknesses:**

1. **Stderr-or-stdout, not both**: `truncate(cr.stderr_trunc or cr.stdout_trunc)` (nodes_po.py:70) passes ONLY stderr if it's non-empty, otherwise stdout. Many tools write useful diagnostics to BOTH streams. The SE never sees stdout if stderr is non-empty.

2. **No diff or change summary**: The SE receives its own prior `proposed_writes.json` contents indirectly (via re-read context files showing original content), but has no explicit summary of what it proposed last time. If the SE needs to understand WHY its writes caused a test failure, it must infer this entirely from the error excerpt.

3. **"Blame the wrong file" risk**: The `FailureBrief` does not reference which files from the proposal are likely responsible for the failure. For multi-file proposals, the SE might fix the wrong file.

4. **Constraints reminder is static**: Each stage has a hardcoded reminder string (e.g., "Global verification must pass before acceptance"). These are not tailored to the specific error and may not help the LLM course-correct.

5. **`verify_failed` command rendered via `" ".join(cmd)`**: This produces `bash scripts/verify.sh` which is fine, but for fallback commands like `["python", "-m", "pytest", "-q"]` it produces `python -m pytest -q`. This is adequate but doesn't quote arguments that might contain spaces.

6. **On `stale_context` retry, context files are re-read**: After rollback, files are back to baseline. The LLM receives fresh hashes. But the SE prompt doesn't explain that the repo was rolled back and hashes have changed — the LLM just sees different hashes than last time with no explanation of why.

### Likelihood of Correct Fix

- **High** for `write_scope_violation` and `stale_context`: the error messages are precise and actionable.
- **Medium** for `acceptance_failed`/`verify_failed`: depends heavily on whether the 2000-char excerpt captures the relevant error. Pytest tracebacks can be verbose; the relevant assertion may be at the end, past the truncation point.
- **Low** for `llm_output_invalid`: the SE sees only the first 500 chars of its prior response + the parse error. If the issue is structural (e.g., extra text around JSON), it may not have enough context to fix it. However, the instructions in the prompt are clear.
- **Very low** for `exception`: the SE can't fix an API failure.

---

## 6) Prompt & Artifact Auditability

### What Is Logged and Where

| Artifact | Saved? | Path | Conditions |
|----------|--------|------|------------|
| SE prompt (full text) | **NO** | — | Never saved anywhere |
| Raw LLM response | **PARTIAL** | `attempt_N/raw_llm_response.json` | Only on parse failure (nodes_se.py:201-204) |
| Parsed WriteProposal | Yes | `attempt_N/proposed_writes.json` | On successful parse (nodes_se.py:209-211) |
| Write result | Yes | `attempt_N/write_result.json` | Always (on scope/hash/write success or failure) |
| Verify results | Yes | `attempt_N/verify_result.json` | Saved on first failure or after all pass |
| Acceptance results | Yes | `attempt_N/acceptance_result.json` | Saved on first failure or after all pass |
| Failure brief | Yes | `attempt_N/failure_brief.json` | On failure (graph.py:103-104 + nodes_se.py:182,205) |
| Command stdout/stderr | Yes | `attempt_N/verify_N_stdout.txt`, etc. | Every command run |
| Run summary | Yes | `run_id/run_summary.json` | At end (if no crash) |
| Work order input | **NO** (not copied to artifacts) | — | Must be reconstructed from original path |
| CLI arguments | **NO** | — | Not saved |
| Context file hashes | **INDIRECT** | Inside `proposed_writes.json` (`base_sha256` per write) | Only for files actually written, not all context files |
| All context file hashes at read time | **NO** | — | Computed in `_read_context_files` but not persisted separately |

### Auditability Gaps

1. **Prompt not persisted**: The full prompt sent to the LLM is constructed at `nodes_se.py:167` and passed directly to `llm.complete()`. It is never written to disk. A postmortem auditor cannot see exactly what the LLM was asked.  
   **Impact**: Cannot distinguish between "the prompt was misleading" and "the LLM ignored a correct prompt".

2. **Raw response discarded on success**: If the LLM's response parses successfully, only the structured `WriteProposal` is saved. Any extra text, comments, or nuances in the raw response are lost.  
   **Impact**: Cannot detect prompt injection, hallucinated metadata, or borderline parse behavior.

3. **Work order not copied to run artifacts**: The work order is loaded from the user-specified path but not copied to `<out>/<run_id>/`. If the original file is later modified or deleted, the run cannot be fully reconstructed.

4. **CLI arguments not recorded**: `--max-attempts`, `--llm-model`, `--llm-temperature`, `--timeout-seconds` are not persisted in `run_summary.json` or anywhere in the artifact tree. The `run_summary.json` includes `run_id` and `work_order_id` but not the operational parameters.

### Is the Run Deterministic?

Given identical inputs (same work order JSON, same repo state, same LLM response), the run would produce identical artifacts and the same `run_id`. However:
- The LLM is fundamentally non-deterministic (even at temperature 0).
- `time.monotonic()` is used for command durations (util.py:99), which vary.
- File system operations (temp file names) are non-deterministic but don't affect outputs.
- The run_id itself IS deterministic: `sha256(canonical_json(wo) + "\n" + baseline)[:16]`.

**Verdict**: Deterministic in all harness-controlled aspects; non-deterministic only in LLM responses and timing.

---

## 7) Security & Safety

### Command Execution

| Property | Status | Evidence |
|----------|--------|----------|
| No `shell=True` | **PASS** | `util.py:106` explicit `shell=False`; `workspace.py:18` explicit `shell=False` |
| Commands split via `shlex.split` | **PASS** for acceptance commands | `nodes_po.py:88` calls `split_command()` → `shlex.split()` |
| Verify commands are hardcoded lists | **PASS** | `nodes_po.py:24-29`: literal list construction |
| Per-command timeout enforced | **PASS** | `util.py:105` passes `timeout` to `subprocess.run` |
| stdout/stderr captured (not inherited) | **PASS** | `util.py:104` uses `capture_output=True` |
| `cwd` always set to repo_root | **PASS** for run_command; git commands use cwd too | `util.py:103`; `workspace.py:16` |

**Injection risks:**
- Acceptance commands come from the work order JSON, which is user-provided. They are split via `shlex.split` and executed without a shell. An attacker who controls the work order can execute arbitrary commands. This is **by design** — the work order is a trusted input.
- The LLM response cannot inject commands: the LLM only proposes file writes, and the harness never executes anything from the LLM's output.
- Verify commands are hardcoded (`nodes_po.py:24-29`) or reference a fixed path (`scripts/verify.sh`). The LLM could write a malicious `scripts/verify.sh` IF that path is in `allowed_files`. This is a policy concern, not a code bug.

### Path Sanitization

| Check | Implemented? | Evidence |
|-------|-------------|----------|
| Paths must be relative | Yes | `schemas.py:23` rejects absolute paths |
| No Windows drive letters | Yes | `schemas.py:26` regex check |
| No `..` traversal after normalization | Yes | `schemas.py:29` |
| Path resolves inside repo | Yes | `util.py:166-170` uses `os.path.realpath` |
| Outdir not inside repo | **NO** | `run.py:18` — no check |

### Secret Handling

- `OPENAI_API_KEY` is read from environment (`llm.py:11`) and passed to the OpenAI client constructor. The key is never logged, printed, or written to artifacts.
- The LLM prompt includes file contents and work-order details. If context files contain secrets, they will be sent to the LLM API and could appear in `proposed_writes.json` if the LLM copies them. **No mitigation exists for secrets in context files.**
- No credential files are created or persisted by the harness.

### Destructive Operation Guardrails

- `git reset --hard` and `git clean -fd` are inherently destructive but guarded by the clean-tree preflight (`run.py:36-42`).
- `git clean -fd` is run on EVERY failure, even when no writes were applied (graph.py:123-124). This is idempotent and safe given the preflight guarantee.
- `git add -A` on PASS stages everything, which modifies the index. Not destructive to the working tree but mutates git state.

---

## 8) Actionable Recommendations

### Priority 1 — Critical (fix before production use)

#### R1. ~~Add top-level exception guard around `graph.invoke()`~~ — COMPLETED
**Status**: **DONE**. Implemented in `run.py:83-128`. `graph.invoke()` is now wrapped in a try/except that: (1) performs best-effort rollback (itself guarded against secondary failure), (2) writes an emergency `run_summary.json` with `verdict: "ERROR"`, exception message, and full traceback, and (3) exits with code 2 to distinguish crashes from normal failures.  
**Failure mode mitigated**: Unhandled exception in any node (rollback failure, shlex parse error, unexpected SDK exception) crashes the process with no summary and a potentially dirty repo.  
**Where**: `run.py:83-128`.  
**Acceptance test**: Inject a `RuntimeError` into a node; verify `run_summary.json` is written and repo is clean afterward.

#### R2. ~~Validate outdir is not inside repo~~ — COMPLETED
**Status**: **DONE**. Implemented in `run.py:44-51` as a preflight check. After the clean-tree check and before `get_baseline_commit`, the harness now rejects with a clear error if `out_dir == repo_root` or `out_dir` is a subdirectory of `repo_root` (both already canonicalized via `os.path.realpath`).  
**Failure mode mitigated**: Artifact data loss on rollback; tree hash pollution on PASS.  
**Where**: `run.py:44-51`.  
**Acceptance test**: Pass `--out <repo>/output`; verify the harness refuses with a clear error message.

#### R3. ~~Use `git clean -fdx` instead of `-fd` for rollback~~ — COMPLETED
**Status**: **DONE**. Changed `workspace.py:81` from `"-fd"` to `"-fdx"`, updated the docstring and error message to match. The docstring now documents why `-fdx` is safe (preflight guarantees a clean tree).  
**Failure mode mitigated**: Gitignored files created by writes survive rollback, leaving the repo dirty.  
**Where**: `workspace.py:81`.  
**Acceptance test**: Write to a `.gitignore`d path, fail PO, verify the file is removed after rollback.  
**Caveat**: `-fdx` removes ALL untracked files including gitignored ones. This is safe because the preflight guarantees a clean tree, and `git status --porcelain` used in `is_clean()` does NOT show gitignored files. If gitignored files pre-exist, they would be destroyed. **Consider**: either also check `git clean -ndx` in preflight to warn about gitignored files, or document the `-fdx` behavior explicitly.

### Priority 2 — High (significant reliability/auditability improvement)

#### R4. ~~Persist the SE prompt to disk~~ — COMPLETED
**Status**: **DONE**. Added in `nodes_se.py` (after `_build_prompt()`, before the LLM call). The full prompt is written as plain text to `attempt_N/se_prompt.txt` on every attempt, including retries.  
**Failure mode mitigated**: Cannot reconstruct what the LLM was asked during postmortem.  
**Where**: `nodes_se.py:170-172`.  
**Acceptance test**: Run a work order; verify `se_prompt.txt` exists in the attempt directory and contains the full prompt.

#### R5. Always persist the raw LLM response — NOT ACTIONED (unnecessary)
**Status**: **Unnecessary**. On successful parse, the LLM's response is already fully captured in `proposed_writes.json` (which contains the parsed `WriteProposal` — the complete structured content of the response). On parse failure, `raw_llm_response.json` was already saved. The additional `raw_llm_response.txt` file introduced by this recommendation was reverted as redundant.  
**Failure mode mitigated**: N/A — the parsed proposal artifact already serves this purpose.  
**Where**: N/A.  
**Acceptance test**: N/A.

#### R6. Catch exceptions in PO node from `shlex.split`
**Failure mode mitigated**: Malformed acceptance command string crashes PO node with no FailureBrief and no rollback guarantee.  
**Where**: `nodes_po.py:88` — wrap `split_command(cmd_str)` in try/except, produce `FailureBrief(stage="exception")`.  
**Acceptance test**: Work order with acceptance command `"echo 'unterminated`; verify FailureBrief is produced.

#### R7. Add timeout to LLM calls
**Failure mode mitigated**: Hangs indefinitely if OpenAI API is unresponsive.  
**Where**: `llm.py:30` — pass `timeout=<timeout_seconds>` to the OpenAI client constructor or the `create()` call.  
**Acceptance test**: Mock a non-responding LLM endpoint; verify the call fails within the timeout.

#### R8. Scope `git add` on PASS to only proposal-touched files
**Failure mode mitigated**: `git add -A` stages verification artifacts and other non-proposal files, polluting the tree hash.  
**Where**: `workspace.py:54` — replace `git add -A` with `git add` of only the specific files from the proposal's `touched_files`.  
**Acceptance test**: Run a PASS scenario where pytest creates `.pytest_cache`; verify tree hash does not include it.

### Priority 3 — Medium (robustness improvements)

#### R9. Copy work order and record CLI args in run artifacts
**Failure mode mitigated**: Cannot fully reconstruct run conditions from artifacts alone.  
**Where**: `run.py`, after loading work order (around line 51). Copy to `run_id/work_order.json`. Add `llm_model`, `llm_temperature`, `max_attempts`, `timeout_seconds` to `run_summary.json`.  
**Acceptance test**: Verify these files/fields exist in a run's artifact directory.

#### R10. Reject proposals with duplicate write paths
**Failure mode mitigated**: Two writes to the same path both pass hash checks (checks run before writes); second write silently overwrites first.  
**Where**: `nodes_tr.py`, after computing `touched_files` (around line 61). Compare `len(touched_files)` to `len(proposal.writes)`.  
**Acceptance test**: Submit a proposal with two writes to the same path; verify `FailureBrief(stage="write_scope_violation")`.

#### R11. Validate `max_attempts >= 1`
**Failure mode mitigated**: `max_attempts=0` still executes one attempt.  
**Where**: `__main__.py`, after parsing args (or in `run.py`).  
**Acceptance test**: Pass `--max-attempts 0`; verify error message.

#### R12. Include BOTH stderr and stdout in FailureBrief excerpt
**Failure mode mitigated**: Current `stderr or stdout` logic loses stdout when stderr is non-empty. Many tools write useful info to both streams.  
**Where**: `nodes_po.py:70,104` — concatenate both (truncated) instead of either/or.  
**Acceptance test**: Create a test that writes to both streams; verify both appear in the FailureBrief excerpt.

### Priority 4 — Low (polish)

#### R13. Enforce `forbidden` list in TR
**Failure mode mitigated**: LLM writes something explicitly forbidden; harness doesn't catch it.  
**Where**: `nodes_tr.py`, after scope check. Check write content for forbidden patterns.  
**Acceptance test**: Submit a write containing a forbidden term; verify rejection.  
**Note**: The spec is ambiguous on whether `forbidden` is advisory or enforced. Document the decision either way.

#### R14. Ship `example_work_order.json`
**Failure mode mitigated**: Spec requires it (AGENTS.md §2.11); currently missing.  
**Where**: Repository root.  
**Acceptance test**: File exists and passes `load_work_order()` validation.

#### R15. Add a global run timeout
**Failure mode mitigated**: A run with many slow commands can run indefinitely. Per-command timeout is enforced, but no cap on total wall-clock time.  
**Where**: `run.py`, around `graph.invoke()`.  
**Acceptance test**: Set global timeout to 5s with a command that sleeps for 10s; verify clean termination.

---

## Appendix A: Detailed Call Graph Narrative

```
__main__.main()
  └─ argparse → args
  └─ run.run_cli(args)
       ├─ os.path.realpath(args.repo, args.work_order, args.out)
       ├─ schemas.load_work_order(path)    → WorkOrder (Pydantic-validated)
       ├─ workspace.is_git_repo(repo)      → bool (git rev-parse)
       ├─ workspace.is_clean(repo)         → bool (git status --porcelain)
       ├─ workspace.get_baseline_commit()  → str (git rev-parse HEAD)
       ├─ util.compute_run_id(wo, baseline)→ str (sha256[:16])
       ├─ graph.build_graph()              → compiled StateGraph
       │    └─ nodes: se, tr, po, finalize
       │    └─ edges: se→{tr,finalize}, tr→{po,finalize}, po→finalize, finalize→{se,END}
       ├─ graph.invoke(initial_state)
       │    │
       │    ├─[se_node]─────────────────────────────────────
       │    │   ├─ _read_context_files(wo, repo)
       │    │   │   └─ util.sha256_file() per file
       │    │   │   └─ open/read with 200KB budget
       │    │   ├─ _build_prompt(wo, ctx, prev_failure_brief)
       │    │   ├─ llm.complete(prompt, model, temp)
       │    │   │   └─ openai.ChatCompletion.create()
       │    │   ├─ llm.parse_proposal_json(raw)
       │    │   │   └─ strip markdown fences → json.loads
       │    │   ├─ schemas.WriteProposal(**parsed)    (Pydantic validation)
       │    │   └─ util.save_json(proposed_writes.json)
       │    │
       │    ├─[tr_node]─────────────────────────────────────
       │    │   ├─ normalize + deduplicate touched paths
       │    │   ├─ scope check: touched ⊆ allowed
       │    │   ├─ path-inside-repo check per file
       │    │   ├─ base_sha256 check per file (ALL before ANY write)
       │    │   ├─ _atomic_write() per file
       │    │   │   └─ mkstemp → write → fsync → os.replace
       │    │   └─ util.save_json(write_result.json)
       │    │
       │    ├─[po_node]─────────────────────────────────────
       │    │   ├─ _get_verify_commands(repo)
       │    │   │   └─ scripts/verify.sh or 3 fallbacks
       │    │   ├─ util.run_command() per verify cmd
       │    │   │   └─ subprocess.run(shell=False, timeout=T)
       │    │   │   └─ write stdout/stderr files
       │    │   ├─ util.split_command(cmd_str) per acceptance cmd
       │    │   ├─ util.run_command() per acceptance cmd
       │    │   └─ util.save_json(verify_result.json, acceptance_result.json)
       │    │
       │    └─[_finalize_node]──────────────────────────────
       │        ├─ record attempt_record
       │        ├─ if FAIL: workspace.rollback(repo, baseline)
       │        │   └─ git reset --hard <baseline>
       │        │   └─ git clean -fd
       │        └─ if PASS: workspace.get_tree_hash(repo)
       │            └─ git add -A
       │            └─ git write-tree
       │
       ├─ util.save_json(run_summary.json)
       └─ print verdict + summary path
```

## Appendix B: Threat Model — Exhaustive Rollback Failure Scenarios

| # | Scenario | Files affected | Rollback covers it? | Notes |
|---|----------|---------------|---------------------|-------|
| 1 | LLM proposes write to tracked file; PO fails | Modified tracked file | **Yes** — `git reset --hard` | Clean recovery |
| 2 | LLM proposes write to new file; PO fails | New untracked file | **Yes** — `git clean -fd` | Unless gitignored (see #5) |
| 3 | LLM proposes write creating new parent dirs; PO fails | New dir + file | **Yes** — `git clean -fd` removes dirs | Unless gitignored |
| 4 | Verify command creates `__pycache__`; PO fails | New gitignored dirs | **Likely yes** — `git clean -fd` removes untracked dirs. `__pycache__` is often in `.gitignore`; `-fd` respects gitignore, so these survive | **GAP** if `.gitignore` matches |
| 5 | LLM writes to a gitignored path (e.g., `.env`); PO fails | New gitignored file | **NO** — survives `git clean -fd` | **GAP** |
| 6 | Write partially applied (2 of 3 files); 3rd write fails | 2 files modified | **Yes** — TR returns `write_failed`, finalize rolls back | But: partial state exists between write and rollback |
| 7 | Process killed (SIGKILL) after writes, before rollback | Files modified, no rollback | **NO** — manual cleanup required | Preflight catches on next run |
| 8 | `git reset --hard` itself fails (e.g., locked index) | Repo dirty | **NO** — `RuntimeError` raised, no summary, repo dirty | **GAP** |
| 9 | Outdir is inside repo; PO fails | Artifact files in repo | `git clean -fd` removes them | **GAP** — artifacts lost |
| 10 | Outdir is inside repo; PASS | Artifact files in repo | `git add -A` stages them | **GAP** — tree hash polluted |

---

*End of audit.*
