# Factory Harness (SE → TR → PO)

A minimal, deterministic **factory harness** that runs a strict **SE → TR → PO** loop using LangGraph:

- **SE (Software Engineer)**: an LLM proposes **direct file writes** (full new contents), as strict JSON.
- **TR (Tool Runner / Applier)**: deterministically validates scope + preconditions and performs **in-situ atomic writes** to the product repo.
- **PO (Verifier / Judge)**: deterministically runs **global verification** + **acceptance commands** and returns PASS/FAIL.

The harness (not the LLM) controls retries, rollback, routing, artifacts, and termination.

## Usage

```bash
python -m factory run \
  --repo /path/to/product \
  --work-order /path/to/work_order.json \
  --out /path/to/outdir \
  --llm-model gpt-4o
```

### Required flags

| Flag | Description |
|------|-------------|
| `--repo` | Path to the product git repo |
| `--work-order` | Path to the work-order JSON file |
| `--out` | Output directory for run artifacts |
| `--llm-model` | LLM model name (e.g. `gpt-4o`, `gpt-4o-mini`) |

### Optional flags

| Flag | Default | Description |
|------|---------|-------------|
| `--max-attempts` | `2` | Maximum SE → TR → PO attempts |
| `--llm-temperature` | `0` | LLM sampling temperature |
| `--timeout-seconds` | `600` | Per-command timeout in seconds |

### Prerequisites

The product repo **must** be a git repo with a **clean working tree** (no staged, unstaged, or untracked changes). The `OPENAI_API_KEY` environment variable must be set.

### Dependencies

Install before use (no `requirements.txt` is shipped per the spec):

```bash
pip install pydantic langgraph openai
```

## How it works

1. **Preflight**: verify git repo + clean tree, record `baseline_commit`.
2. **Attempt loop** (up to `--max-attempts`):
   - **SE**: build prompt from work order + context files + prior failure, call LLM, parse `WriteProposal`.
   - **TR**: validate scope (`writes[*].path ⊆ allowed_files`), validate `base_sha256` hashes, apply atomic file writes.
   - **PO**: run global verification, then acceptance commands. Any nonzero exit → FAIL.
3. **On FAIL**: rollback via `git reset --hard <baseline>` + `git clean -fd`, retry if attempts remain.
4. **On PASS**: leave changes in repo (no auto-commit), compute `repo_tree_hash_after`.

## Global verification (§6.5)

If `scripts/verify.sh` exists in the product repo, run:

- `bash scripts/verify.sh`

Otherwise, run in order:

1. `python -m compileall -q .`
2. `python -m pip --version`
3. `python -m pytest -q`

## Artifacts

Each run produces artifacts under `<out>/<run_id>/`:

```
<out>/<run_id>/
  run_summary.json
  attempt_1/
    proposed_writes.json
    write_result.json
    verify_result.json
    acceptance_result.json
    failure_brief.json          # (if failed)
    verify_0_stdout.txt
    verify_0_stderr.txt
    acceptance_0_stdout.txt
    acceptance_0_stderr.txt
    ...
  attempt_2/
    ...
```

## Deterministic run ID

`run_id` = first 16 hex chars of `sha256(canonical_json(work_order) + "\n" + baseline_commit)`.

## Size limits

| Limit | Value |
|-------|-------|
| Max file write size | 200 KB per file |
| Max total write size | 500 KB across all files |
| Max context files | 10 files |
| Max context read budget | 200 KB total |
| Max error excerpt | 2000 chars |

## Assumptions

- `context_files` must be a strict subset of `allowed_files`. This is enforced during work-order validation to ensure the LLM only reads files it is allowed to write.
- Global verification fallback always runs `pytest`; if `pytest` is not installed, verification fails unless `scripts/verify.sh` exists.
- An empty `factory/__init__.py` is created (not listed in the spec's file tree but required for reliable Python package imports).
- Acceptance commands are split via `shlex.split` and executed without a shell.
- On failure, rollback is always performed (even if no writes were applied) since it is idempotent and safe.
- `repo_tree_hash_after` is computed by staging all changes (`git add -A`) then running `git write-tree`. Changes remain staged but not committed.

## Package tree

```
factory/
  __init__.py        # package marker (empty)
  __main__.py        # argparse wiring → run.run_cli()
  graph.py           # LangGraph StateGraph, routing, finalize node
  llm.py             # thin OpenAI Chat Completions wrapper
  nodes_po.py        # PO: verify + acceptance commands
  nodes_se.py        # SE: prompt construction + LLM call + parse
  nodes_tr.py        # TR: scope/hash checks + atomic writes
  run.py             # CLI orchestration, preflight, RunSummary
  schemas.py         # Pydantic models + load helpers
  util.py            # hashing, truncation, JSON IO, command runner
  workspace.py       # git helpers (is_clean, rollback, tree hash)
```
