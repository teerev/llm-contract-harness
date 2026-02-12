Now I have complete code-truth. Here's the report.

---

# Technical Reconnaissance Report: Baseline-Anchored, State-Aware Planning

## 1) EXECUTIVE SUMMARY

The repo is **structurally 70% ready** for this change. Key pieces already in place:

- `--repo` already triggers `_build_repo_file_listing()` which walks the target repo at HEAD, producing a `set[str]` of relative paths. This set flows into chain validation (`validate_plan_v2`) and `compute_verify_exempt`.
- Bootstrap WO filtering already exists: `compile_plan()` lines 424-451 detect WOs whose postconditions create `scripts/verify.sh` and skip them if the file exists in `repo_file_listing`. Renumbering is handled by `_renumber_work_orders()`.
- The template has an unused `{{REPO_HINTS}}` placeholder (replaced with empty string today) — a natural injection point for repo state.
- Canonical run directories, run.json, compile_summary.json all exist with extensible schema.

**What's missing:**
- No git baseline resolution in the planner (it reads the filesystem directly, not a specific commit).
- No repo snapshot artifact (file listing is ephemeral — used in-memory, never persisted).
- No prompt augmentation with repo state (the LLM doesn't know what files exist).

**Top 3 scope-creep risks:**
1. **Baseline resolution requires git dependency in the planner.** The planner currently has zero git dependency — `_build_repo_file_listing()` is a pure `os.walk`. Adding `--baseline` means importing `resolve_commit` / `get_baseline_commit` from `factory/workspace.py` or duplicating them. This creates a cross-subsystem dependency.
2. **Prompt augmentation scope.** If we inject repo state into the prompt via `{{REPO_HINTS}}`, the LLM may change its output structure. This could silently break validation or produce unexpected WO sequences. Needs careful scoping (existence flags only, or full file listing?).
3. **Compile hash invalidation.** The compile hash currently hashes `spec + template + model + reasoning_effort`. Adding repo state as a prompt input changes the LLM's output but does NOT change the compile hash unless we add repo state to the hash. This would break content-addressability semantics.

## 2) CURRENT PLANNER PIPELINE (CODE-TRUTH)

### CLI entrypoints

- **`planner/__main__.py`**: calls `planner.cli.main()`
- **`planner/cli.py`**: `build_parser()` defines flags. `_run_compile()` calls `compile_plan()`.
  - `--spec` (required), `--outdir` (optional), `--repo` (optional), `--template` (optional), `--artifacts-dir` (optional), `--overwrite` (flag), `--print-summary` (flag), `--verbose`/`--quiet`/`--no-color`
  - `--repo` flows as `repo_path` parameter to `compile_plan()`. When `None`, repo file listing is empty set.

### `compile_plan()` flow (`planner/compiler.py`)

**Sequence:**
1. Resolve template path — `resolve_template_path()` (`prompt_template.py`)
2. Resolve artifacts root — `resolve_artifacts_root()` (`shared/run_context.py`)
3. Generate ULID run_id, create `{artifacts_root}/planner/{run_id}/` with `exist_ok=False`
4. Create `compile/` and `output/` subdirs
5. Read spec + template as bytes
6. Compute compile hash — `_compute_compile_hash(spec_bytes, template_bytes, DEFAULT_MODEL, DEFAULT_REASONING_EFFORT)` — SHA-256, truncated to 16 hex chars. **Repo state is NOT included.**
7. Write early `run.json` (started_at_utc, config, inputs with spec/template SHA-256)
8. Build repo file listing — `_build_repo_file_listing(repo_path)` if `repo_path` provided, else empty set. **Pure `os.walk`, no git, reads HEAD working tree.**
9. Render prompt — `render_prompt(template_text, spec_text)`. **`{{REPO_HINTS}}` replaced with empty string.**
10. Compile loop (up to `MAX_COMPILE_ATTEMPTS = 3`):
    - Write `prompt_attempt_{N}.txt`
    - Call LLM — `client.generate_text(prompt)` returns `LLMResult(text, reasoning)`
    - Write `llm_raw_response_attempt_{N}.txt`, `llm_reasoning_attempt_{N}.txt`
    - Parse JSON — `_parse_json()`, write `manifest_raw_attempt_{N}.json`
    - Validate — `parse_and_validate()` (E0xx) + `validate_plan_v2()` (E1xx/W1xx, uses `repo_file_listing`)
    - Write `validation_errors_attempt_{N}.json`
    - If errors and not last attempt: build revision prompt, continue
11. Post-loop: `compute_verify_exempt(final_work_orders, verify_contract, repo_file_listing)`
12. Bootstrap filtering (lines 424-451): if `VERIFY_SCRIPT` in `repo_file_listing`, filter WOs whose postconditions create it, renumber via `_renumber_work_orders()`
13. Build manifest, inject provenance, write canonical output + optional export
14. Write `compile_summary.json` and finalize `run.json`

### Compile hash inputs

`_compute_compile_hash()`: `spec_bytes + "\n" + template_bytes + "\n" + model + "\n" + reasoning_effort`. **Deterministic, content-addressable. Does NOT include repo state, baseline, or run_id.**

### OpenAI call path

`planner/openai_client.py`: `OpenAIResponsesClient.generate_text()` → `_submit_and_poll()`. Payload: `model=gpt-5.2-codex`, `reasoning={"effort": "medium", "summary": "auto"}`, `max_output_tokens=64000`, `background=True`. Posted to `https://api.openai.com/v1/responses`.

### Bootstrap filtering

`compiler.py` lines 424-451. Detection: checks `VERIFY_SCRIPT = "scripts/verify.sh"` in postcondition paths of each WO. Condition: `repo_file_listing` is non-empty AND contains `VERIFY_SCRIPT`. **Only fires when `--repo` is passed and the file physically exists in the working tree at the time of the compile.** Not git-baseline-aware.

### Artifact directory scheme

`{artifacts_root}/planner/{ULID}/` with `exist_ok=False` (collision = `FileExistsError`). Subdirs: `compile/` (per-attempt artifacts), `output/` (canonical WO files). Immutable — never overwritten.

## 3) CURRENT FACTORY INTERFACES THAT MATTER FOR PLANNING

### Verification command selection (`factory/nodes_po.py`)

`_get_verify_commands()`: checks `os.path.isfile(os.path.join(repo_root, VERIFY_SCRIPT_PATH))`. If present: `[["bash", "scripts/verify.sh"]]`. Otherwise: `VERIFY_FALLBACK_COMMANDS` (compileall + pip --version + pytest). When `verify_exempt=True`: uses `VERIFY_EXEMPT_COMMAND` (compileall only).

**Implication for planner:** The planner must know whether `scripts/verify.sh` exists at baseline to correctly set `verify_exempt` and to avoid emitting a bootstrap WO whose `file_absent` precondition will fail.

### Factory preflight (`factory/run.py` + `workspace.py`)

Checks: `is_git_repo` → `has_commits` → `ensure_git_identity` → `current_branch_name` (reject detached HEAD) → `is_clean`. Then resolves baseline (HEAD or `--commit-hash`), creates/reuses working branch via `ensure_working_branch()`.

### Factory artifacts the planner could consume

`run_summary.json`: `run_id`, `work_order_id`, `verdict`, `baseline_commit`, `baseline_source`, `working_branch`, `repo_tree_hash_after`, `attempts[]`. `run.json`: adds `planner_ref`, `git_workflow`, `inputs.work_order_sha256`.

**Planner-to-factory provenance exists:** factory reads `provenance.planner_run_id` from WO JSON. **Factory-to-planner provenance does NOT exist.** The planner has no mechanism to "plan from last factory run" — it doesn't read factory artifacts.

## 4) WHAT "REPO STATE SNAPSHOT" SHOULD MEAN

### Minimal snapshot proposal

A **repo state snapshot** should be a small JSON file capturing:

```json
{
  "baseline_commit": "abc123...",         // resolved 40-char SHA
  "baseline_source": "HEAD",             // or "commit-hash"
  "tree_hash": "def456...",              // git write-tree (deterministic)
  "key_files": {
    "scripts/verify.sh": {"exists": true, "sha256": "..."},
    "requirements.txt": {"exists": true, "sha256": "..."},
    "setup.py": {"exists": false}
  },
  "file_count": 42,
  "file_listing": ["README.md", "scripts/verify.sh", ...]
}
```

### Detection of verify.sh

Use `git ls-tree` at the baseline commit rather than `os.path.isfile()`. This makes it deterministic and baseline-anchored: `git ls-tree {baseline} -- scripts/verify.sh`. Returns empty if absent, hash+mode if present.

### Key files allowlist

Put in `planner/defaults.py` as `SNAPSHOT_KEY_FILES: tuple[str, ...] = ("scripts/verify.sh",)`. Extensible later for `requirements.txt`, `pyproject.toml`, etc. Only these files get individual existence/hash entries. The full file listing uses the existing `_build_repo_file_listing` logic but scoped to `git ls-tree -r --name-only {baseline}` instead of `os.walk`.

### Avoiding huge repos

`git ls-tree -r --name-only {baseline}` is cheap (reads the git object store, no working tree I/O). For the prompt, inject only `key_files` status + total count, not the full listing.

## 5) REQUIRED NEW BEHAVIOR (SPEC CANDIDATE)

### Planner CLI

- **`--baseline <commit-ish>`**: optional. Resolves to full SHA via `resolve_commit()`. Default: HEAD of `--repo` (if provided). **Requires `--repo` to be set** (fail fast otherwise).
- No `--snapshot-mode` needed initially — snapshot is always generated when `--repo` is provided.

### Baseline resolution

Reuse `factory/workspace.py` helpers (`resolve_commit`, `get_baseline_commit`, `has_commits`) OR extract them to `shared/` to avoid planner→factory import. **Recommendation: move `resolve_commit` and `get_baseline_commit` to `shared/run_context.py`** (they're pure git wrappers with no factory-specific logic).

### Baseline artifacts

Write early (before LLM call):
- `{run_dir}/compile/baseline.json`: `{baseline_commit, baseline_source, tree_hash}`
- `{run_dir}/compile/repo_snapshot.json`: `{key_files, file_count, file_listing}`

### Prompt augmentation

Replace `{{REPO_HINTS}}` in `render_prompt()` with a formatted summary:
```
## Baseline Repository State
Commit: abc123...
Key files present: scripts/verify.sh
Total files: 42
```

This requires changing `render_prompt()` to accept an optional `repo_hints: str` parameter instead of always replacing with empty string.

### Validation / bootstrap filtering

No changes needed — the existing bootstrap filtering (postcondition-based detection + `repo_file_listing` check) already works. The only change: `repo_file_listing` should be built from `git ls-tree` at the baseline instead of `os.walk`, making it deterministic and baseline-anchored.

### Artifact directory structure

No changes. Run dirs are already ULID-based and immutable. The new `baseline.json` and `repo_snapshot.json` go into the existing `compile/` subdir.

## 6) EXACT FILES TO TOUCH + ESTIMATED PATCH SIZE

- **`shared/run_context.py`** — new helpers: `resolve_commit()`, `get_baseline_commit()`, `git_ls_tree_listing()` (extracted from factory/workspace.py pattern). **Small (~30 LOC)**
- **`planner/defaults.py`** — new constant: `SNAPSHOT_KEY_FILES`. **Tiny (~3 LOC)**
- **`planner/compiler.py`** — add `--baseline` resolution, snapshot generation, `{{REPO_HINTS}}` population, replace `os.walk` with git-based listing, add `baseline.json`/`repo_snapshot.json` writes, add fields to `run.json`/`compile_summary.json`. **Medium (~60 LOC)**
- **`planner/prompt_template.py`** — change `render_prompt()` to accept optional `repo_hints` parameter. **Tiny (~5 LOC)**
- **`planner/cli.py`** — add `--baseline` flag, pass to `compile_plan()`. **Tiny (~5 LOC)**
- **`factory/workspace.py`** — extract `resolve_commit`/`get_baseline_commit` to shared (or add import indirection). **Small (~10 LOC moved)**
- **`tests/planner/test_compile_loop.py`** — update mocks for new `compile_plan()` parameter. **Small (~10 LOC)**
- **`tests/test_defaults_canonical.py`** — update defaults count. **Tiny (~1 LOC)**

**Total estimated delta:** ~125 LOC net new across ~8 files.

## 7) LANDMINES / AMBIGUITIES TO RESOLVE BEFORE IMPLEMENTATION

1. **Planner has zero git dependency today.** `_build_repo_file_listing()` uses `os.walk`. Adding `--baseline` requires git. **Decision needed:** make git optional (fall back to os.walk when `--baseline` is not provided) or require git when `--repo` is provided?

2. **Cross-package import direction.** `resolve_commit` and `get_baseline_commit` live in `factory/workspace.py`. The planner should not import from factory. **Decision needed:** move to `shared/run_context.py` or duplicate?

3. **Compile hash stability.** If repo state enters the prompt via `{{REPO_HINTS}}`, the template text changes per-repo, but `compile_hash` uses the **raw template bytes** (before substitution). So the compile hash remains stable. However, if we add baseline commit to the hash, same spec + different baseline = different hash. **Decision needed:** include baseline in compile hash?

4. **`--repo` without git.** Currently `--repo /some/dir` works on any directory (not just git repos). Adding `--baseline` would fail. **Decision needed:** require `--repo` to be a git repo only when `--baseline` is also provided, or always?

5. **Detached HEAD in planner context.** The factory rejects detached HEAD. Should the planner also reject it, or just use whatever HEAD resolves to? **Recommendation:** allow detached HEAD in planner (it's read-only).

6. **`os.walk` vs `git ls-tree` parity.** `os.walk` sees untracked files and respects `SKIP_DIRS`. `git ls-tree` only sees tracked files. Switching would change the `repo_file_listing` content, potentially affecting chain validation and `compute_verify_exempt`. **Decision needed:** is this acceptable, or should we keep `os.walk` as default and only use `git ls-tree` when `--baseline` is provided?

7. **Test assertions on `compile_summary.json` fields.** Adding `baseline_commit`, `repo_snapshot` to the summary may break tests that assert exact key counts or structures. The `TestCompileSummary` test class in `test_compile_loop.py` checks `summary["success"]` and `summary["compile_attempts"]` but not exhaustive key sets. **Low risk.**

8. **Existing `VERIFY_SCRIPT` string duplication.** The planner's bootstrap filter uses a local `VERIFY_SCRIPT = "scripts/verify.sh"` (compiler.py line 429), while the factory uses `VERIFY_SCRIPT_PATH` from `factory/defaults.py`, and the planner has its own `VERIFY_SCRIPT_PATH` in `planner/defaults.py` (line 75). **Three copies of the same string.** Consider consolidating into `shared/`.