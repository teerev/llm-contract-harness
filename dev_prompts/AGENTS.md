# AGENTS.md — Single Source of Truth (READ FIRST)

## 0) Mission (what you are building)
Build a minimal, deterministic **factory harness** in Python that runs a strict **SE → TR → PO** loop using LangGraph:

- **SE (Software Engineer)**: an LLM proposes **DIRECT FILE WRITES** (full new contents for each touched file), as strict JSON.
- **TR (Tool Runner / Applier)**: deterministically validates scope + preconditions and performs **in-situ atomic writes** to the product repo.
- **PO (Verifier / Judge)**: deterministically runs **global verification** + **acceptance commands** and returns PASS/FAIL.
- The harness (not the LLM) controls retries, rollback, routing, artifacts, and termination.

This is a harness, not “autonomous SWE”. **Credibility > cleverness.**

## 1) Read-first rule (MANDATORY)
You must read this entire file before writing any code.
If anything is ambiguous, choose the simplest valid implementation and document it in README → “Assumptions”.

## 2) Non-negotiables (MANDATORY)

### 2.1 Determinism & execution hygiene
- All command execution is done by a deterministic Python subprocess runner (not by the LLM).
- Never use `shell=True`.
- Always capture `stdout`, `stderr`, `exit_code`.
- Always enforce a timeout per command.
- Always run commands with `cwd=repo_root`.
- Use stable sorting for any file listings.
- Never use nondeterministic IDs (no uuid4). Use deterministic `run_id` derived from input hashes.
- Never fetch from the network except for the LLM API call itself (if used).

### 2.2 Git-only + clean-tree preflight (safety + credibility)
- The product repo **must** be a Git repo.
- The working tree must be **clean** before starting:
  - no staged changes
  - no unstaged changes
  - no untracked files
- If not Git or not clean: hard error with a clear message, and do not modify anything.

### 2.3 In-situ edits with strict rollback (transaction semantics)
No workspace copying in this minimal version.

- Record `baseline_commit = git rev-parse HEAD` at preflight.
- Each attempt must start from `baseline_commit`.
- On any failure after applying writes, rollback deterministically:
  - `git reset --hard <baseline_commit>`
  - `git clean -fd`
- WARNING: `git clean -fd` is destructive. This is why clean-tree preflight is mandatory.

### 2.4 Sharp boundary: LLM proposes, harness decides
- The LLM may only propose **direct writes** + brief summary.
- The LLM never runs commands and never decides whether failures matter.
- Routing/termination decisions are deterministic and implemented in the harness.

### 2.5 Verification is law (always enforced)
For every attempt:
1) Run **global verification** (see §6.5 exact command rules).
2) Then run `acceptance_commands` from the work order (in order).
Any nonzero exit code is failure.

### 2.6 Write scope enforcement (computed from proposal, not LLM “claims”)
- Work orders provide `allowed_files` as explicit relative paths (no globs).
- The harness must compute touched files from the proposal and enforce:
  - proposed file paths ⊆ allowed_files
- If violation: reject with stage `write_scope_violation`.

### 2.7 Stale-context protection (precondition hashes; MANDATORY)
Direct writes are dangerous without a precondition.

- For every file the LLM proposes to write, it must include `base_sha256` of the file content it *believes* it is editing.
- TR must compute the current file SHA256 in the repo before writing.
- If mismatch: reject with stage `stale_context` and do NOT write anything.
- This prevents “LLM edited an older version and overwrote a newer one”.

### 2.8 Structured failure feedback (bounded)
- Convert failures into a bounded `FailureBrief` for SE retries.
- Never paste full logs into the LLM prompt.
- Store full logs to files; pass only a short excerpt.

### 2.9 Minimal dependencies
- Dependencies: stdlib + `pydantic` + `langgraph` + whatever is strictly required to call the LLM.
- No DB, no queue, no remote VM logic.

### 2.10 Required package tree (MUST MATCH EXACTLY)
Create exactly these files under `factory/` (no extra Python modules unless absolutely required; if required, justify in README):

factory/
- __main__.py
- graph.py
- llm.py
- nodes_po.py
- nodes_se.py
- nodes_tr.py
- run.py
- schemas.py
- util.py
- workspace.py  (git helpers + rollback; NOT a temp workspace copier)

### 2.11 File creation boundary (strict)
- Do not create any files outside `factory/` except:
  - `README.md`
  - `example_work_order.json`
- Do not create: `requirements.txt`, `pyproject.toml`, lockfiles, additional docs, CI configs, or extra folders.

### 2.12 LLM client contract (strict)
- Implement `factory/llm.py` using the official `openai` Python package only.
- Use **Chat Completions** (not Responses API).
- Call `client.chat.completions.create(...)` and return `choices[0].message.content` as raw string.
- No streaming, no tool calls, no multi-message handling.
- Read `OPENAI_API_KEY` from environment; fail fast if missing.
- If the `openai` package cannot be imported: raise RuntimeError with a clear message.
- Use model name from `--llm-model`, temperature from `--llm-temperature`.

## 3) CLI requirements (must implement)
Command:

`python -m factory run --repo /path/to/product --work-order /path/to/work_order.json --out /path/to/outdir`

Flags:
- `--max-attempts` (default 2)
- `--llm-model` (string; required)
- `--llm-temperature` (default 0)
- `--timeout-seconds` (default 600)

Behavior:
- Print final verdict and the path to `<out>/<run_id>/run_summary.json`.

## 4) Schemas (Pydantic) — minimal, strict (define in factory/schemas.py)

### 4.1 WorkOrder
Fields:
- `id: str`
- `title: str`
- `intent: str`
- `allowed_files: list[str]`  (explicit relative paths only)
- `forbidden: list[str]`
- `acceptance_commands: list[str]`
- `context_files: list[str]`
- `notes: str | None = None`

Validation rules:
- For each path in `allowed_files` and `context_files`:
  - must be relative
  - must not contain drive letters
  - normalized path must not start with `..`
  - must not be absolute
- `acceptance_commands` must be non-empty.
- `context_files` must be bounded:
  - max 10 files
  - max 200 KB total read bytes (truncate context file reading deterministically if needed)
- Simplest rule for subset:
  - require `context_files ⊆ allowed_files` (strict and safe). Document in README.

### 4.2 WriteProposal (DIRECT WRITES)
Define:

- `summary: str`
- `writes: list[FileWrite]`

Where `FileWrite` has:
- `path: str`  (relative, normalized)
- `base_sha256: str`  (hex sha256 of the current file content; for new files use sha256 of empty bytes)
- `content: str`  (full new file content; text only)

Rules:
- `writes` must be non-empty.
- No binary writes.
- Enforce size limits deterministically (suggested: max 200 KB per file, max 500 KB total) and document in README.

### 4.3 FailureBrief
- `stage: str` (one of stages below)
- `command: str | None`
- `exit_code: int | None`
- `primary_error_excerpt: str`
- `constraints_reminder: str`

Allowed stages:
- `preflight`
- `llm_output_invalid`
- `write_scope_violation`
- `stale_context`
- `write_failed`
- `verify_failed`
- `acceptance_failed`
- `exception`

### 4.4 CmdResult
- `command: list[str]`
- `exit_code: int`
- `stdout_trunc: str`
- `stderr_trunc: str`
- `stdout_path: str`
- `stderr_path: str`
- `duration_seconds: float`

### 4.5 AttemptRecord
- `attempt_index: int`
- `baseline_commit: str`
- `proposal_path: str`
- `touched_files: list[str]`
- `write_ok: bool`
- `verify: list[CmdResult]`
- `acceptance: list[CmdResult]`
- `failure_brief: FailureBrief | None`

### 4.6 RunSummary
Same as before, but record `repo_tree_hash_after` only on PASS (or always if you prefer; must be deterministic).

## 5) Deterministic IDs and hashing (must implement)
(unchanged from prior spec)

## 6) Core behavior spec (MUST FOLLOW)

### 6.1 Preflight steps
(unchanged from prior spec)

### 6.2 Attempt loop
For attempt_index in 1..max_attempts:

1) **SE node**:
   - Construct an SE prompt that includes:
     - WorkOrder intent + constraints
     - allowed_files list
     - forbidden list
     - context_files contents (bounded)
     - FailureBrief if present
     - explicit instruction: output JSON with keys `writes` and `summary` only
     - explicit instruction: include `base_sha256` for every write
   - Call LLM via `llm.complete()`.
   - Strictly parse output:
     - must parse to WriteProposal
     - writes must be non-empty
   - If parse fails: FailureBrief(stage="llm_output_invalid") and this attempt is a FAIL with no repo changes.

2) **TR node**:
   - Compute touched files from `writes[*].path`.
   - Normalize touched file paths.
   - Enforce touched_files ⊆ allowed_files.
     - If violation: FailureBrief(stage="write_scope_violation") and FAIL.
   - For each write:
     - Read current file bytes if exists else empty bytes.
     - Compute sha256 and compare to `base_sha256`.
     - If mismatch: FailureBrief(stage="stale_context") and FAIL (do not write anything).
   - Apply writes atomically:
     - Write to a temp file in the same directory, fsync if simplest, then rename/replace.
     - Ensure parent dirs exist only if the file path is allowed and inside repo.
   - If any write fails: FailureBrief(stage="write_failed") and FAIL.
   - Record write_result.json.

3) **PO node**:
   - Run global verification commands (see §6.5).
   - If any verify command fails: FailureBrief(stage="verify_failed") and FAIL.
   - If verify passes, run acceptance commands in order.
   - If any acceptance command fails: FailureBrief(stage="acceptance_failed") and FAIL.
   - If all pass: PASS.

4) **On FAIL after any writes**:
   - Roll back deterministically:
     - `git reset --hard <baseline_commit>`
     - `git clean -fd`
   - Continue if attempts remain.

5) **On PASS**:
   - Leave changes in repo (no auto-commit).
   - Compute `repo_tree_hash_after`.
   - End success.

### 6.5 EXACT global verification command rules (NO AMBIGUITY)
(unchanged from prior spec)

### 6.6 Acceptance command execution rules
(unchanged from prior spec)

## 7) Artifacts spec (minimal, exact)
Write artifacts under:

`<out>/<run_id>/attempt_<N>/`

Per attempt:
- `proposed_writes.json` (exact WriteProposal JSON)
- `write_result.json` (write_ok, touched_files, any errors)
- `verify_result.json`
- `acceptance_result.json`
- `failure_brief.json` (if fail)
- Full logs for each command (stdout/stderr files)

At end write:
- `<out>/<run_id>/run_summary.json`

## 8) LangGraph requirements (graph structure)
Same structure, but state fields align to direct writes:
- proposal (WriteProposal) not diff
- write_ok not apply_ok

## 9) File responsibilities (must follow)
- `schemas.py`: pydantic models + load/save helpers.
- `util.py`: hashing, truncation, json IO, canonical json bytes, command runner, path validation helpers.
- `workspace.py`: git helpers (is_git_repo, is_clean, baseline, rollback).
- `llm.py`: thin LLM wrapper + strict parsing helper.
- `nodes_se.py`: SE prompt construction + LLM call + parse to WriteProposal.
- `nodes_tr.py`: scope checks + base hash checks + atomic file writes + write_result emission.
- `nodes_po.py`: run verify + acceptance + FailureBrief extraction.
- `graph.py`: LangGraph definition and routing.
- `run.py`: CLI entry logic, orchestrates run, artifact dirs, run_summary.
- `__main__.py`: argparse wiring, calls run.run_cli().

## 10) Implementation order (MANDATORY)
(unchanged)

## 11) Output contract (MANDATORY)
(unchanged)

## 12) Compliance checklist (self-check before final output)
- [ ] Only required `factory/` files were created (plus README + example JSON).
- [ ] `python -m factory --help` wiring is correct.
- [ ] No `shell=True` anywhere.
- [ ] Git repo + clean-tree preflight enforced.
- [ ] Rollback uses baseline commit + `git clean -fd`.
- [ ] Scope enforcement uses writes[*].path ⊆ allowed_files.
- [ ] Stale-context enforced via base_sha256 checks.
- [ ] Global verify command rules match §6.5 exactly.
- [ ] Acceptance commands use shlex.split and never a shell.
- [ ] Artifacts emitted to the specified paths.
- [ ] FailureBrief is bounded; full logs written to files; only excerpts sent to LLM.
