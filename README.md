## Factory harness (SE → TR → PO)

This repo contains a minimal, deterministic **factory harness** that runs a strict **SE → TR → PO** loop using LangGraph:

- **SE**: calls an LLM to propose a **unified diff** (JSON keys: `unified_diff`, `summary` only)
- **TR**: validates patch scope from diff headers and applies via `git apply`
- **PO**: runs **global verification** then work-order **acceptance commands**

### Usage

Run:

```bash
python -m factory run --repo /path/to/product --work-order /path/to/work_order.json --out /path/to/outdir --llm-model MODEL_NAME
```

Optional flags:
- `--max-attempts` (default 2)
- `--llm-temperature` (default 0)
- `--timeout-seconds` (default 600)

The product repo **must** be a git repo with a **clean working tree**. On failures after an attempt starts, the harness rolls back in-situ using:

- `git reset --hard <baseline_commit>`
- `git clean -fd`

### Global verification (exact)

If `scripts/verify.sh` exists in the product repo, run:

- `bash scripts/verify.sh`

Else run, in order:

1. `python -m compileall -q .`
2. `python -m pip --version`
3. `python -m pytest -q`

### Assumptions

- `context_files` must be a strict subset of `allowed_files`.
- Global verification fallback always runs `pytest`; if `pytest` is not installed, verification fails unless `scripts/verify.sh` exists.

