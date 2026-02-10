# llm-compiler

A **deterministic contract layer** between LLM planning and LLM code execution.

This repository splits autonomous software engineering into two isolated stages connected by a validated, machine-readable contract:

1. **Planner** -- an LLM decomposes a product spec into a sequence of work orders with preconditions, postconditions, file scopes, and acceptance tests.
2. **Factory** -- a separate LLM executes each work order inside a deterministic harness (SE → TR → PO) that enforces scope, validates hashes, runs acceptance, and rolls back on failure.

The work order JSON is the contract surface. Both sides parse it through the same Pydantic schema (`factory/schemas.py`). The planner validates the chain at compile time; the factory re-checks every constraint at runtime. LLM non-determinism cannot bypass deterministic enforcement.

## Quick start

```bash
pip install pydantic langgraph openai httpx
export OPENAI_API_KEY=sk-...
```

### Compile a spec into work orders

```bash
python -m planner compile \
  --spec spec.txt \
  --outdir wo/ \
  --repo /path/to/product
```

This calls the planner LLM (up to 3 attempts with automatic self-correction), validates the output against structural checks (E0xx) and cross-work-order chain checks (E1xx), computes `verify_exempt` flags, and writes individual `WO-*.json` files plus a manifest.

### Execute a single work order

```bash
python -m factory run \
  --repo /path/to/product \
  --work-order wo/WO-01.json \
  --out artifacts/ \
  --llm-model gpt-4o
```

The factory preflight-checks the git repo, then runs an attempt loop: the SE LLM proposes file writes as JSON, the TR node validates scope and content hashes then applies atomic writes, and the PO node runs global verification and acceptance commands. On failure, the repo is rolled back and the loop retries.

## Planner CLI

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--spec` | yes | | Product spec text file |
| `--outdir` | yes | | Output dir for `WO-*.json` and manifest |
| `--repo` | no | *(empty repo)* | Target product repo for precondition validation |
| `--template` | no | `planner/PLANNER_PROMPT.md` | Prompt template path |
| `--artifacts-dir` | no | `./artifacts` | Compile artifacts (prompts, raw LLM responses, errors) |
| `--overwrite` | no | `false` | Replace existing work orders in outdir |
| `--print-summary` | no | `false` | Print one-line summary per WO to stdout |

Exit codes: `0` success, `1` general error, `2` validation error, `3` API/network error, `4` JSON parse error.

## Factory CLI

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--repo` | yes | | Product git repo (must be clean) |
| `--work-order` | yes | | Work order JSON file |
| `--out` | yes | | Output dir for run artifacts |
| `--llm-model` | yes | | LLM model name (e.g. `gpt-4o`) |
| `--max-attempts` | no | `2` | Max SE → TR → PO attempts |
| `--llm-temperature` | no | `0` | LLM sampling temperature |
| `--timeout-seconds` | no | `600` | Per-command timeout |

## How it works

```
  Product Spec                    Work Orders (JSON)                Product Repo
       │                                │                                │
       ▼                                ▼                                ▼
  ┌─────────┐   validate + emit    ┌─────────┐   execute + verify   ┌─────────┐
  │ Planner │ ──────────────────►  │ WO-*.json│ ──────────────────► │  Repo   │
  │  (LLM)  │   E0xx, E1xx chain  │ contract │   SE → TR → PO      │ (git)   │
  └─────────┘   checks            └─────────┘   deterministic       └─────────┘
       │                                │           harness              │
       │  retry with                    │                                │
       │  revision prompt               │                                │  rollback
       └────────┘                       │                                │  on failure
                                        ▼                                └────────┘
                                  Shared schema:
                                  factory/schemas.py
```

**Planner stage:** Render prompt template with spec, call LLM, parse JSON, normalize, validate (structural + chain), retry with error feedback if invalid, compute `verify_exempt`, write `WO-*.json`.

**Factory stage per work order:**
1. Preflight: verify clean git tree, check preconditions.
2. SE node: render prompt with work order + context files + prior failure brief, call LLM, parse `WriteProposal`.
3. TR node: validate all write paths against `allowed_files`, batch-check `base_sha256` hashes, apply atomic file writes.
4. PO node: run global verification (or `compileall` if verify-exempt), check postconditions, run acceptance commands.
5. On failure: rollback via `git reset --hard`, retry if attempts remain. On pass: leave changes in repo.

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

factory/              Deterministic execution harness
  graph.py            LangGraph state machine (SE → TR → PO)
  nodes_se.py         SE: prompt + LLM call + parse WriteProposal
  nodes_tr.py         TR: scope/hash checks + atomic writes
  nodes_po.py         PO: verify + acceptance commands
  schemas.py          Shared Pydantic models (WorkOrder, WriteProposal, etc.)
  workspace.py        Git helpers (clean check, rollback, tree hash)
  run.py              CLI orchestration, preflight, RunSummary
```

See [INVARIANTS.md](INVARIANTS.md) for the complete list of non-negotiable system constraints and where each is enforced.
