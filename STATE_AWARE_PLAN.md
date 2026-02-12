# State-Aware Planner Upgrade: Plan + Milestones

## A) Code-Truth Baseline

Every claim below was verified by reading the actual source files in this repo.

### Compile hash computation

- **File:** `planner/compiler.py`, function `_compute_compile_hash()` (lines 51-66)
- **Inputs hashed (in order):** `spec_bytes`, `"\n"`, `template_bytes`, `"\n"`, `model`, `"\n"`, `reasoning_effort`
- **Algorithm:** SHA-256, truncated to `COMPILE_HASH_HEX_LENGTH` (16) hex chars
- **Called at:** line 237-239 with `spec_bytes, template_bytes, DEFAULT_MODEL, DEFAULT_REASONING_EFFORT`
- **Evidence:** Baseline commit is NOT included. Repo state is NOT included.

### Repo file listing (os.walk, not git)

- **File:** `planner/compiler.py`, function `_build_repo_file_listing()` (lines 94-105)
- **Mechanism:** `os.walk(repo_path)` with `SKIP_DIRS` pruning. Returns `set[str]` of relative POSIX paths.
- **Called at:** lines 274-277: `if repo_path: repo_file_listing = _build_repo_file_listing(repo_path)`
- **Evidence:** Pure filesystem walk. Sees untracked files, dirty working tree changes, and gitignored files (except those in `SKIP_DIRS`). Not deterministic across working-tree states.

### {{REPO_HINTS}} placeholder

- **Defined in:** `planner/defaults.py` line 79: `OPTIONAL_PLACEHOLDERS = ("{{DOCTRINE}}", "{{REPO_HINTS}}")`
- **Handled in:** `planner/prompt_template.py`, `render_prompt()` (lines 20-33): optional placeholders are replaced with empty string (line 32)
- **Evidence in template:** Searched `planner/PLANNER_PROMPT.md` for `REPO_HINTS` — **NOT FOUND**. The placeholder is defined in defaults and handled by the render function, but it does not actually appear in the current template file. Only `{{PRODUCT_SPEC}}` appears (line 65 of PLANNER_PROMPT.md).
- **Implication:** To inject repo hints, we must BOTH add `{{REPO_HINTS}}` to the template AND change `render_prompt()` to accept a value for it.

### Bootstrap WO filtering

- **File:** `planner/compiler.py`, lines 424-451
- **Detection rule:** Checks if `"scripts/verify.sh"` is in a WO's postcondition paths (lines 436-440). Local constant `VERIFY_SCRIPT = "scripts/verify.sh"` (line 429).
- **Trigger condition:** `repo_file_listing` is non-empty AND contains `VERIFY_SCRIPT` (line 433)
- **Renumbering:** `_renumber_work_orders()` (lines 113-119) — reassigns IDs as `WO-{i:02d}` contiguously from 1.
- **Audit fields:** `bootstrap_skipped` and `bootstrap_reason` on `CompileResult`, written to both `compile_summary.json` and `run.json`.

### Duplicate VERIFY_SCRIPT_PATH constants

- `planner/defaults.py` line 75: `VERIFY_SCRIPT_PATH = "scripts/verify.sh"`
- `planner/compiler.py` line 429: local `VERIFY_SCRIPT = "scripts/verify.sh"`
- `factory/defaults.py` line 81: `VERIFY_SCRIPT_PATH = "scripts/verify.sh"`
- Three copies of the same string.

### Factory git helpers (potential reuse)

- **File:** `factory/workspace.py`
- `resolve_commit(repo_root, commitish)` (lines 186-199): `git rev-parse --verify {commitish}^{commit}` → full SHA. Raises `ValueError`.
- `get_baseline_commit(repo_root)` (lines 47-55): `git rev-parse HEAD` → full SHA. Raises `RuntimeError`.
- `has_commits(repo_root)` (lines 180-183): `git rev-parse --verify HEAD` → bool.
- `is_git_repo(repo_root)` (lines 33-36): `git rev-parse --is-inside-work-tree` → bool.
- All use internal `_git()` helper which wraps `subprocess.run(["git"] + args, cwd=cwd, ...)`.
- **Dependency direction:** These live in `factory/`. Planner must NOT import from factory.

### Planner has zero git dependency today

- Searched all planner/ files for `subprocess`, `git`, `import factory` — none found.
- `_build_repo_file_listing()` is pure `os.walk`.
- The planner currently works on non-git directories when `--repo` is passed.

### Validation functions consuming repo_file_listing

- `validate_plan_v2()` in `planner/validation.py` (line 481): parameter `repo_file_listing: set[str]`. Initializes `file_state: set[str] = set(repo_file_listing)` at line 510. Used for precondition satisfiability checks.
- `compute_verify_exempt()` in `planner/validation.py` (line 677): parameter `repo_file_listing: set[str]`. Initializes `file_state: set[str] = set(repo_file_listing)` at line 699.
- Both consume the same `set[str]` type. Switching from os.walk to git ls-tree is transparent as long as the output type stays `set[str]`.

### Test that exercises --repo

- `tests/planner/test_compile_loop.py`, `test_compile_with_repo` (line 297): creates a `tmp_path/"repo"` plain directory (NOT a git repo), writes `existing.py`, calls `compile_plan(repo_path=str(repo))`. Asserts success.
- **Evidence:** This test will break if we enforce `--repo` must be a git repo. Must be updated.

---

## B) Proposed Design

### CLI flags

| Flag | Status | Semantics |
|------|--------|-----------|
| `--repo` | Existing, optional | If provided: must be a git repo with at least 1 commit. Fail fast otherwise. |
| `--baseline` | **New**, optional | Only valid with `--repo`. Resolves commit-ish to full SHA. Default: HEAD. |

**Failure modes:**
- `--repo /not/a/git/repo` → `ERROR: /not/a/git/repo is not a git repository. The planner requires --repo to point to a git repo.`
- `--repo /empty/repo` (no commits) → `ERROR: /empty/repo has no commits. At least one commit is required for baseline resolution.`
- `--baseline abc123` without `--repo` → `ERROR: --baseline requires --repo.`
- `--baseline nonexistent` → `ERROR: Cannot resolve 'nonexistent' to a commit: ...`

### Git helper location

**New file: `shared/git.py`**

Extract these pure git wrappers (currently in `factory/workspace.py`) into a shared module:
- `is_git_repo(path) -> bool`
- `has_commits(path) -> bool`
- `resolve_commit(path, commitish) -> str`
- `get_head_commit(path) -> str`
- `git_ls_tree_files(path, commit) -> set[str]`  **(new)**
- `git_ls_tree_blob_sha(path, commit, filepath) -> str | None`  **(new)**

`factory/workspace.py` would import from `shared/git.py` instead of defining its own copies. The internal `_git()` helper moves to `shared/git.py`.

**Why a separate file:** `shared/run_context.py` is about artifacts/ULID/hashing. Git operations are a distinct concern. Keeps both files focused.

### Snapshot schema

**`baseline.json`** (written to `{run_dir}/compile/baseline.json`):

```json
{
  "baseline_commit": "5df4fc10f56d0c868d536dbd9595f5e7365fff82",
  "baseline_source": "HEAD",
  "repo_path": "/absolute/path/to/repo"
}
```

**`repo_snapshot.json`** (written to `{run_dir}/compile/repo_snapshot.json`):

```json
{
  "baseline_commit": "5df4fc10f56d0c868d536dbd9595f5e7365fff82",
  "file_count": 42,
  "key_files": {
    "scripts/verify.sh": {
      "exists": true,
      "blob_sha": "a1b2c3d4e5f6..."
    }
  },
  "file_listing": [
    "README.md",
    "scripts/verify.sh",
    "src/main.py"
  ]
}
```

- `key_files` uses a fixed allowlist from `planner/defaults.py`: `SNAPSHOT_KEY_FILES = ("scripts/verify.sh",)`.
- `file_listing` is the sorted output of `git ls-tree -r --name-only {baseline}`. Capped at 10,000 entries for safety.
- `blob_sha` is the git blob SHA from `git ls-tree`, not a rehash. Cheap — parsed from the same ls-tree output.

### Compile hash update

**New input order:**

```
SHA-256(
  spec_bytes + "\n" +
  template_bytes + "\n" +
  model + "\n" +
  reasoning_effort + "\n" +
  baseline_commit    # 40-char hex SHA, or empty string if no --repo
)
```

- When `--repo` is NOT provided: `baseline_commit` is empty string → hash is backward-compatible with existing behavior (empty string + "\n" was never part of the old hash, so old and new hashes differ — this is acceptable since the old hashes were under a different directory scheme anyway).
- When `--repo` IS provided: same spec + different baseline → different hash. This is the desired A1 behavior.

### Prompt augmentation

Add `{{REPO_HINTS}}` to `planner/PLANNER_PROMPT.md` in a new section after the product spec block. Change `render_prompt()` signature to accept `repo_hints: str = ""`. When `--repo` is provided, render:

```
## Baseline Repository State

- Baseline commit: 5df4fc10f56d
- Tracked file count: 42
- scripts/verify.sh: present
```

When `--repo` is NOT provided, `{{REPO_HINTS}}` is replaced with empty string (current behavior preserved).

---

## C) Milestones

### M1: shared/git.py + CLI validation

**Goal:** Git helpers in shared/; planner enforces `--repo` is a git repo; `--baseline` flag added.

**Files to touch:**
- `shared/git.py` (new) — extract `_git()`, `is_git_repo()`, `has_commits()`, `resolve_commit()`, `get_head_commit()` from `factory/workspace.py`
- `factory/workspace.py` — change to import from `shared/git.py`
- `planner/cli.py` — add `--baseline` flag
- `planner/compiler.py` — add `baseline` parameter to `compile_plan()`, add git-repo validation in preflight

**Acceptance:**
- `python -m planner compile --spec spec.txt --repo /tmp/not-a-repo` → fails with clear message
- `python -m planner compile --spec spec.txt --baseline HEAD` (no --repo) → fails with "requires --repo"
- `python -m pytest tests/` → all existing tests pass (update `test_compile_with_repo` to use a real git repo fixture)

### M2: git-based repo listing + snapshot artifacts

**Goal:** Replace `os.walk` with `git ls-tree` at baseline; write `baseline.json` and `repo_snapshot.json`.

**Files to touch:**
- `shared/git.py` — add `git_ls_tree_files()`, `git_ls_tree_blob_sha()`
- `planner/compiler.py` — replace `_build_repo_file_listing()` call with git-based listing; write baseline.json + repo_snapshot.json before LLM call
- `planner/defaults.py` — add `SNAPSHOT_KEY_FILES` constant

**Acceptance:**
- After compile with `--repo`: `{run_dir}/compile/baseline.json` exists with correct commit SHA
- After compile with `--repo`: `{run_dir}/compile/repo_snapshot.json` exists with `key_files`, `file_count`, `file_listing`
- Bootstrap filtering still works: compile with `--repo` pointing to repo WITH `scripts/verify.sh` → bootstrap WO skipped
- `python -m pytest tests/` → passes

### M3: compile hash includes baseline

**Goal:** Update `_compute_compile_hash()` to include baseline commit.

**Files to touch:**
- `planner/compiler.py` — update `_compute_compile_hash()` signature and call site

**Acceptance:**
- Two compiles with same spec but different baselines → different `compile_hash` in run.json
- Compile without `--repo` → compile_hash uses empty string for baseline component
- `python -m pytest tests/` → update compile_hash assertions in `test_compiler_extras.py`

### M4: prompt injection via {{REPO_HINTS}}

**Goal:** LLM sees baseline repo state in the prompt.

**Files to touch:**
- `planner/PLANNER_PROMPT.md` — add `{{REPO_HINTS}}` section
- `planner/prompt_template.py` — change `render_prompt()` to accept `repo_hints` parameter
- `planner/compiler.py` — build repo_hints string, pass to `render_prompt()`

**Acceptance:**
- Compile with `--repo`: `prompt_attempt_1.txt` contains "Baseline Repository State" with commit SHA and key file status
- Compile without `--repo`: `prompt_attempt_1.txt` does NOT contain "Baseline Repository State"
- `python -m pytest tests/planner/test_prompt_template.py` → passes

### M5: test cleanup + doc update

**Goal:** All tests green, defaults count updated, CONFIG_DEFAULTS.md regenerated, README updated.

**Files to touch:**
- `tests/planner/test_compile_loop.py` — update `test_compile_with_repo` to use a git repo fixture
- `tests/planner/test_compiler_extras.py` — update compile_hash tests
- `tests/test_defaults_canonical.py` — update planner defaults count
- `docs/CONFIG_DEFAULTS.md` — regenerate
- `README.md` — document `--baseline` flag in planner CLI table

**Acceptance:**
- `python -m pytest tests/` → 523+ tests pass
- `python tools/dump_defaults.py --check` → passes

---

## D) Risk Register

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| 1 | **Compile hash backward incompatibility.** Adding baseline to hash means ALL old compile hashes become unreproducible. | Medium | Acceptable: old hashes were under the prior directory scheme (hash-based dirs). New scheme uses ULID dirs; compile hash is metadata only, not a path component. |
| 2 | **Test brittleness from git-repo requirement.** The existing `test_compile_with_repo` test uses a plain directory (not a git repo). Enforcing git will break it. | Low | Update test to use a proper git init + commit fixture. Other test fixtures already do this in factory tests (`init_git_repo` in `tests/factory/conftest.py`). |
| 3 | **Dependency direction: planner → factory.** If we import git helpers from `factory/workspace.py`, we create a wrong-direction dependency. | High | Extract to `shared/git.py`. Factory imports from shared. Planner imports from shared. Neither imports from the other. |
| 4 | **Large repo performance.** `git ls-tree -r --name-only` on a repo with 100K+ files could be slow or produce huge `repo_snapshot.json`. | Low | Cap `file_listing` at 10,000 entries (truncate with a flag). `key_files` check is O(1) per key file. `file_count` is just `len(listing)`. |
| 5 | **Prompt injection size.** If repo hints are too verbose, they waste tokens and could push the prompt over limits. | Low | Hints are minimal: 3-5 lines (commit, file count, key file existence). No full listing in prompt. |
| 6 | **os.walk vs git ls-tree parity.** Switching from os.walk to git ls-tree changes what files are visible (untracked files disappear). This affects chain validation — a precondition `file_exists("foo.txt")` where foo.txt is untracked would now fail. | Medium | Document the change: with `--repo`, only tracked files at the baseline commit are considered. This is correct behavior for baseline-anchored planning. Users who need untracked files should commit them first. |
| 7 | **PLANNER_PROMPT.md does not contain {{REPO_HINTS}}.** The placeholder is defined in defaults but absent from the template. Adding it changes the template content, which changes the compile hash for ALL compiles (even without --repo). | Medium | Accept this as a one-time break. The template is versioned in git; the hash change is expected when the template changes. |
| 8 | **Three copies of VERIFY_SCRIPT_PATH.** `planner/defaults.py`, `planner/compiler.py` (local), `factory/defaults.py`. | Low | Out of scope for this change. Note for future: consolidate to `shared/constants.py`. |
