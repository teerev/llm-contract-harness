# llm-compiler

A **structurally enforced** contract layer between LLM planning and LLM code execution.

This repository splits autonomous software engineering into two isolated stages connected by a validated, machine-readable contract:

1. **Planner** -- an LLM decomposes a product spec into a sequence of work orders with preconditions, postconditions, file scopes, and acceptance tests.
2. **Factory** -- a separate LLM executes each work order inside a structural enforcement harness (SE → TR → PO) that validates scope, checks content hashes, runs acceptance commands, and rolls back on failure.

The work order JSON is the contract surface. Both sides parse it through the same Pydantic schema (`factory/schemas.py`). The planner validates the chain at compile time; the factory re-checks every constraint at runtime. Deterministic validation of file scope, path safety, and content hashes ensures that LLM non-determinism cannot bypass structural enforcement.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
export OPENAI_API_KEY=sk-...
```

### Full pipeline (plan + execute all work orders)

```bash
llmch pipeline \
  --seed spec.txt \
  --repo /path/to/local/repository \
  --branch factory/my-feature \
  --create-branch
```

This compiles the spec into work orders, then executes them sequentially against the repo. Each passing work order is committed to the branch. Stops on the first failure.

### Compile a spec into work orders

```bash
llmch plan --spec spec.txt --outdir wo
```

This calls the planner LLM (up to 5 attempts with automatic self-correction), validates the output against structural checks (E0xx) and cross-work-order chain checks (E1xx), computes `verify_exempt` flags, and writes individual `WO-*.json` files plus a manifest.

All compile artifacts (prompts, LLM responses, reasoning traces, validation errors, and the normalized manifest) are written to a canonical, immutable run directory under `./artifacts/planner/{run_id}/`. Each run gets a unique ULID-based `run_id` and a `run.json` manifest suitable for DB indexing.

If `--outdir` is provided, work order files are also exported there for convenience. The canonical copy under `./artifacts/` is the authoritative record.

### Execute a single work order

```bash
llmch run \
  --repo /path/to/product \
  --work-order wo/WO-01.json
```

The factory preflight-checks the git repo, creates a working branch, then runs an attempt loop: the SE LLM proposes file writes as JSON, the TR node validates scope and content hashes then applies atomic writes, and the PO node runs global verification and acceptance commands. On failure, the repo is rolled back and the loop retries. On pass, changes are committed and pushed.

All run artifacts (work order snapshot, per-attempt SE prompts, write results, verify/acceptance output, failure briefs, and run summary) are written to `./artifacts/factory/{run_id}/`. Each run gets its own immutable directory with a `run.json` manifest.

### Run all work orders in sequence

```bash
./utils/run_work_orders.sh \
  --wo-dir wo/ \
  --target-repo /path/to/product \
  --artifacts-dir ./artifacts
```

This initializes a clean git repo, creates a single session branch, then executes each `WO-*.json` in order on that branch. Each passing WO is committed to the same branch. Stops on the first failure.

## Artifact layout

Both tools write to a shared canonical artifacts root (default `./artifacts/`, override with `--artifacts-dir` or the `ARTIFACTS_DIR` environment variable):

```
artifacts/
├── planner/
│   └── {run_id}/                    # ULID — unique, sortable by time
│       ├── run.json                 # DB-indexable run manifest
│       ├── compile/                 # per-attempt compile artifacts
│       │   ├── prompt_attempt_1.txt
│       │   ├── prompt_attempt_2.txt # revision prompt with error feedback
│       │   ├── llm_raw_response_attempt_1.txt
│       │   ├── llm_reasoning_attempt_1.txt
│       │   ├── manifest_raw_attempt_1.json
│       │   ├── validation_errors_attempt_1.json
│       │   └── ...
│       └── output/                  # canonical work order files
│           ├── WO-01.json
│           ├── WO-02.json
│           └── WORK_ORDERS_MANIFEST.json
└── factory/
    └── {run_id}/                    # ULID — unique, sortable by time
        ├── run.json                 # DB-indexable run manifest
        ├── work_order.json          # input snapshot
        ├── run_summary.json         # detailed run summary
        └── attempt_1/
            ├── se_prompt.txt
            ├── proposed_writes.json
            ├── write_result.json
            └── ...
```

Each `run.json` contains the run ID, UTC timestamps, config snapshot, SHA-256 hashes of key inputs/outputs, tool version (git commit), and provenance linkage. Factory `run.json` files include a `planner_ref` that traces back to the planner run that produced the work order.

Run directories are immutable -- no run ever overwrites another. The `run_id` is a 26-character ULID (timestamp + random), so runs sort chronologically in directory listings.

## Planner CLI

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--spec` | yes | | Product spec text file |
| `--outdir` | no | *(canonical only)* | Optional export dir for `WO-*.json` and manifest |
| `--repo` | no | *(empty repo)* | Target product repo for precondition validation |
| `--template` | no | `planner/PLANNER_PROMPT.md` | Prompt template path |
| `--artifacts-dir` | no | `./artifacts` or `$ARTIFACTS_DIR` | Canonical artifacts root |
| `--overwrite` | no | `false` | Replace existing work orders in outdir |
| `--print-summary` | no | `false` | Print one-line summary per WO to stdout |

Exit codes: `0` success, `1` general error, `2` validation error, `3` API/network error, `4` JSON parse error.

## Factory CLI

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--repo` | yes | | Product git repo (must be clean, at least one commit) |
| `--work-order` | yes | | Work order JSON file |
| `--llm-model` | no | `gpt-5.2` | LLM model name |
| `--artifacts-dir` | no | `./artifacts` or `$ARTIFACTS_DIR` | Canonical artifacts root |
| `--out` | no | *(canonical only)* | Optional export dir for run artifacts |
| `--branch` | no | *(auto-generated)* | Working branch name (reuse if exists, create if not) |
| `--reuse-branch` | no | `false` | Require `--branch` to already exist (resume mode) |
| `--create-branch` | no | `false` | Require `--branch` to NOT exist (new session mode) |
| `--commit-hash` | no | `HEAD` | Baseline commit (start-point for new branches) |
| `--no-push` | no | `false` | Disable git push after commit |
| `--max-attempts` | no | `2` | Max SE → TR → PO attempts |
| `--llm-temperature` | no | `0` | LLM sampling temperature |
| `--timeout-seconds` | no | `600` | Per-command timeout |
| `--allow-verify-exempt` | no | `false` | Honor `verify_exempt=true` in work orders (M-22) |

## Git workflow

The factory manages git branches, commits, and pushes automatically. It never commits directly to `main` or `master`.

### Branch lifecycle

On every invocation the factory checks out a **working branch** before making any changes. Branch selection happens exactly once at the start of the run; commit and push operations never create or switch branches.

**Default behavior (no `--branch`):** The factory auto-generates a collision-safe branch name:
- With planner provenance: `factory/{planner_run_id}/{session_ulid}`
- Without: `factory/adhoc/{session_ulid}`

**Explicit branch (`--branch X`):** Use branch `X`. If it exists, resume on it. If it doesn't, create it from the baseline commit. Override this auto-detection with `--reuse-branch` or `--create-branch`.

### Common patterns

```bash
# New session, auto-named branch (simplest usage)
python -m factory run --repo ./product --work-order wo/WO-01.json

# Explicit new branch
python -m factory run --repo ./product --work-order wo/WO-01.json --branch feature/my-session --create-branch

# Resume: run a second work order on the same branch
python -m factory run --repo ./product --work-order wo/WO-02.json --branch feature/my-session --reuse-branch

# Auto reuse-or-create (default when --branch is given)
python -m factory run --repo ./product --work-order wo/WO-02.json --branch feature/my-session

# Start from a specific commit instead of HEAD
python -m factory run --repo ./product --work-order wo/WO-01.json --commit-hash abc123

# Skip pushing (local-only work)
python -m factory run --repo ./product --work-order wo/WO-01.json --no-push
```

### Commit and push

On a passing verdict the factory commits all changes to the working branch with the message `{WO-ID}: applied by factory (run {run_id})`. It then pushes the branch to the default remote with `git push -u`. Push failures do not change the verdict -- the run is still PASS, but a warning is printed with a remediation command.

To disable pushing, pass `--no-push`. The batch runner (`run_work_orders.sh`) uses `--no-push` by default so all work orders accumulate on a single branch before any push.

### Fail cases

The factory fails fast with a clear error and suggested fix for these git issues:

| Situation | Error | Fix |
|-----------|-------|-----|
| Repo has no commits | *"has no commits ... requires at least one commit"* | `cd repo && git add -A && git commit -m 'init'` |
| Detached HEAD | *"detached HEAD state ... requires a named branch"* | `cd repo && git checkout -b <branch>` |
| Uncommitted changes | *"has uncommitted changes ... must be clean"* | `cd repo && git stash` or `git reset --hard` |
| `--branch main` | *"protected ... never commit directly to main/master"* | Use a different branch name |
| `--reuse-branch` but branch missing | *"does not exist (--reuse-branch requires ...)"* | Drop `--reuse-branch` or create the branch first |
| `--create-branch` but branch exists | *"already exists (--create-branch requires ...)"* | Drop `--create-branch` or use a different name |
| `--reuse-branch` + `--create-branch` | *"mutually exclusive"* | Pick one |
| `--reuse-branch` without `--branch` | *"requires --branch"* | Add `--branch <name>` |
| `--commit-hash` doesn't resolve | *"Cannot resolve ... to a commit"* | Check the hash or ref name |

### Git identity

The factory sets `user.name` and `user.email` using `git config --local` so commits work in repos without global git config. This never touches the user's global config. Defaults: `llm-compiler` / `llm-compiler@noreply.local` (configurable in `factory/defaults.py`).

## Security notice

**Acceptance commands and LLM-authored code run unsandboxed** with the
operator's full privileges. The factory uses `subprocess.run(shell=False)`,
which prevents shell metacharacter injection but does not restrict what
executables the LLM invokes or what those executables do. An adversarial or
confused LLM can read arbitrary files, make network requests, or modify the
host system.

**Run the factory inside a disposable container** (or equivalent sandbox)
whenever processing untrusted specs, untrusted work orders, or using LLM
models whose output cannot be fully trusted. Do not run on machines with
access to production secrets, SSH keys, or cloud credentials without
network isolation.

## Prerequisites

- **Git** installed and on `PATH`. The target repo must have at least one
  commit.
- **Python 3.10+** on `PATH` with `pip` and `pytest` available (used by
  the default verify fallback).
- **`OPENAI_API_KEY`** environment variable set.
- **Unrestricted HTTPS access** to `api.openai.com` (no proxy support).
- **Sole-writer access** to the target repo. The factory assumes it is the
  only process modifying the repo during execution. There is no file
  locking; concurrent modification is undefined behavior.
- **Disposable target repo.** The factory runs `git reset --hard` +
  `git clean -fdx` on failure. Do not point `--repo` at a repo with
  uncommitted work you want to keep.

## Guarantees

The following properties hold under the prerequisites above:

- **Enforcement checks are deterministic.** Given the same LLM output, the
  same work order, and the same repo state, path validation, scope checks,
  base-hash checks, and precondition/postcondition gates produce the same
  verdict every time. No hidden state, no randomness.
- **LLM non-determinism cannot bypass structural enforcement.** No matter
  what the LLM outputs, it cannot write to files outside `allowed_files`,
  write to paths outside the repo root, skip the hash check, or avoid
  acceptance command execution.
- **The planner validates work-order chains.** The compile-retry loop with
  E0xx/E1xx error codes, cross-work-order precondition/postcondition
  tracking, and `verify_exempt` computation is a genuine validation
  pipeline.
- **The factory rolls back on failure** (under normal process lifecycle).
  `BaseException` handler and finalize-node rollback restore the repo to
  baseline. Tested and verified.
- **Comprehensive artifact trail.** Every attempt writes prompts, proposals,
  write results, verify/acceptance output, and failure briefs. Each run
  directory is immutable (ULID-based, never overwritten) and includes a
  `run.json` manifest with SHA-256 hashes, UTC timestamps, config snapshots,
  and planner-to-factory provenance linkage.
- **523 tests** cover enforcement invariants, including adversarial-audit
  hardening (M-01–M-10) and credibility fixes (M-20–M-23).

## How it works

```
  Product Spec                    Work Orders (JSON)                Product Repo
       │                                │                                │
       ▼                                ▼                                ▼
  ┌─────────┐   validate + emit   ┌───────-──┐   execute + verify     ┌─────────┐
  │ Planner │ ──────────────────► │ WO-*.json│ ─────────────────---─► │  Repo   │
  │  (LLM)  │   E0xx, E1xx chain  │ contract │   SE → TR → PO         │ (git)   │
  └─────────┘   checks            └────────-─┘   structural           └─────────┘
       │                                │           enforcement          │
       │  retry with                    │                                │
       │  revision prompt               │                                │  rollback
       └────────┘                       │                                │  on failure
                                        ▼                                └────────┘
                                  Shared schema:
                                  factory/schemas.py
```

**Planner stage:** Render prompt template with spec, call LLM, parse JSON, normalize, validate (structural + chain), retry with error feedback if invalid, compute `verify_exempt`, write `WO-*.json`.

**Factory stage per work order:**
1. Preflight: verify clean git tree, pull latest, create or reuse working branch.
2. SE node: render prompt with work order + context files + prior failure brief, call LLM, parse `WriteProposal`.
3. TR node: validate all write paths against `allowed_files`, batch-check `base_sha256` hashes, apply atomic file writes.
4. PO node: run global verification (or `compileall` if verify-exempt), check postconditions, run acceptance commands.
5. On failure: rollback via `git reset --hard`, retry if attempts remain. On pass: commit to working branch, push.

## Validation error codes

| Code | Scope | Rule |
|------|-------|------|
| E000 | Structural | Empty/malformed work orders list |
| E001 | Per-WO | ID format or contiguity violation |
| E003 | Per-WO | Shell operator in acceptance command |
| E004 | Per-WO | Glob character in path |
| E005 | Per-WO | Pydantic schema validation failure |
| E006 | Per-WO | Python syntax error in `python -c` command |
| E101 | Chain | Precondition not satisfiable by cumulative state |
| E102 | Chain | Contradictory preconditions (exists + absent) |
| E103 | Chain | Postcondition path not in `allowed_files` |
| E104 | Chain | `allowed_files` entry has no postcondition |
| E105 | Chain | `bash scripts/verify.sh` in acceptance (banned) |
| E106 | Chain | Verify contract never satisfied by plan |
| W101 | Chain | Acceptance command depends on missing file (warning) |

## Size limits

| Limit | Value |
|-------|-------|
| Max file write | 200 KB per file |
| Max total writes | 500 KB across all files |
| Max context files | 10 per work order |

## Package layout

```
planner/              Factory contract compiler
  cli.py              Argparse, exit codes
  compiler.py         Compile loop: prompt → LLM → validate → revise → write
  validation.py       Structural + chain validators, verify_exempt
  prompt_template.py  Template loading and rendering
  io.py               Atomic file writes, overwrite logic
  openai_client.py    OpenAI Responses API (background polling)

factory/              Structural enforcement harness
  graph.py            LangGraph state machine (SE → TR → PO)
  nodes_se.py         SE: prompt + LLM call + parse WriteProposal
  nodes_tr.py         TR: scope/hash checks + atomic writes
  nodes_po.py         PO: verify + acceptance commands
  schemas.py          Shared Pydantic models (WorkOrder, WriteProposal, etc.)
  workspace.py        Git helpers (clean check, rollback, tree hash)
  run.py              CLI orchestration, preflight, RunSummary

shared/               Cross-subsystem infrastructure
  run_context.py      ULID generation, SHA-256 helpers, run.json management,
                      artifacts root resolution, tool version detection
```

## Limitations

The following are known, documented constraints — not bugs:

- **No semantic correctness guarantee.** The enforcement layer validates
  structural properties (paths, hashes, scope, schema). It cannot verify
  that LLM-generated code implements the human intent correctly. The LLM
  can satisfy all checks with stubs or hardcoded outputs.
- **No host isolation.** Acceptance commands and LLM-authored `verify.sh`
  content execute with the operator's privileges. See the Security Notice
  above. A container or sandbox is required for untrusted workloads.
- **No crash recovery.** If the process is killed (SIGKILL, OOM, power
  loss) during file writes, the repo may be left in a partially modified
  state. The preflight `is_clean` check blocks the next run, but manual
  recovery (`git reset --hard && git clean -fdx`) is required.
- **No idempotent re-execution.** Re-running a completed work order calls
  the LLM again and may produce different code. The system does not detect
  or skip already-completed work.
- **No byte-reproducible artifacts.** Artifact metadata (timestamps,
  durations, absolute paths, ULID-based run IDs) varies across runs. LLM
  output is nondeterministic. The `compile_hash` is deterministic (content-
  addressable), but `run_id` and all other artifact content may differ.
- **Supervised operation assumed.** The system is designed for operator-
  supervised execution, not unattended deployment. There is no logging
  framework, no cost tracking, and no health-check mechanism.
- **The `forbidden` field is prompt guidance, not enforcement.** The
  factory does not mechanically block writes that the `forbidden` list
  describes. If a file is in both `allowed_files` and `forbidden`, the
  LLM can write to it.

See [INVARIANTS.md](INVARIANTS.md) for the complete list of non-negotiable system constraints and where each is enforced.
