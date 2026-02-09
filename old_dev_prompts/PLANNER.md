# CURSOR CODING TASK — Build `planner` (Work-Order Planner / Plan Compiler)

You are an expert Python engineer working inside an existing repo that already contains a `factory` package executed as:

    python -m factory

Your job is to build a **separate, minimal** Python package called `planner` that can be executed as:

    python -m planner

This package compiles a single user-provided product-spec text file into a sequence of validated Work Order JSON files (`WO-01.json`, `WO-02.json`, …) by calling the OpenAI API.

IMPORTANT CONSTRAINTS:
- Keep `planner` and `factory` strictly separate.
- Do NOT introduce any wrapper that runs both.
- Do NOT reference any names not present in the repo (e.g. no “frink”).
- DO NOT write or modify any tests. Code only.
- Freeze the work order format exactly as defined in `schemas.py`. Do not extend it.

───────────────────────────────────────────────────────────────────────────────
## Existing Work Order Schema (MUST USE, DO NOT MODIFY)

There is already a pydantic model in `schemas.py`:

class WorkOrder(BaseModel):
    id: str
    title: str
    intent: str
    allowed_files: list[str]
    forbidden: list[str]
    acceptance_commands: list[str]
    context_files: list[str]
    notes: Optional[str] = None
    ... validators ...

Your planner MUST output work orders that validate with this model.
Import and use this model directly for validation.

───────────────────────────────────────────────────────────────────────────────
## Required LLM Output Format (STRICT)

The LLM response MUST be a **single JSON object** and nothing else.

Top-level JSON format:

{
  "system_overview": [string, ...],     // optional but recommended
  "work_orders": [                      // required
    { WorkOrder-compatible object }, ...
  ]
}

CRITICAL:
- Each object inside "work_orders" MUST validate against WorkOrder schema.
- Do NOT include extra keys inside individual work orders.
- Reject any non-JSON output.

───────────────────────────────────────────────────────────────────────────────
## Planner-Level Validation Rules (in addition to WorkOrder)

1) IDs and ordering
- id must match `^WO-\d{2}$`
- work orders must be contiguous starting from WO-01
- manifest order defines execution order
- output filename must match id exactly (WO-01.json contains id "WO-01")

2) Global verification command
- Enforce that `./scripts/verify.sh` appears in acceptance_commands for every work order.

3) Path hygiene
- Reject any path in allowed_files or context_files containing `*`, `?`, `[`.

4) Normalization
- Strip whitespace from all string fields.
- Deduplicate list entries preserving order.

If validation fails:
- Do NOT write WO-*.json
- Write validation_errors.json
- Exit non-zero

───────────────────────────────────────────────────────────────────────────────
## CLI Interface — `python -m planner`

Implement argparse-based CLI with subcommand:

    python -m planner compile --spec SPEC.txt --outdir DIR
                             [--template TEMPLATE.md]
                             [--artifacts-dir DIR]
                             [--overwrite]
                             [--print-summary]

IMPORTANT:
- `--outdir` is REQUIRED. Do not provide a default.
- This outdir is intended to be the input directory for factory in a later step.

Overwrite behavior:
- If `--outdir` exists and contains any files matching `WO-*.json` or `WORK_ORDERS_MANIFEST.json`,
  then refuse to run unless `--overwrite` is provided.
- If `--overwrite` is provided, delete only `WO-*.json` and `WORK_ORDERS_MANIFEST.json` in that directory
  before writing new outputs. Do not delete unrelated files.

Defaults:
- --template:
    - use `./examples/CREATE_WORK_ORDERS_PROMPT.md` if it exists
    - otherwise require explicit --template
- --artifacts-dir:
    - `./examples/artifacts` if it exists
    - else `./artifacts`

Console output should be minimal and factual:
- compile hash
- number of work orders
- outdir written
- artifacts directory
- on failure, point to validation_errors.json

Exit codes:
- 0 success
- 2 validation error
- 3 API error
- 4 JSON parse error

───────────────────────────────────────────────────────────────────────────────
## Prompt Template Handling

Read a template file and inject product spec text.

Required placeholder:
- {{PRODUCT_SPEC}}

Optional placeholders (replace with empty string if absent):
- {{DOCTRINE}}
- {{REPO_HINTS}}

Implementation: simple string replacement.

Template MUST instruct model:
- output a single JSON object and nothing else
- use exact WorkOrder field names
- keep each work order low-entropy (few files)
- include `./scripts/verify.sh` in acceptance_commands for each work order

───────────────────────────────────────────────────────────────────────────────
## OpenAI API Requirements

Use OpenAI Responses API.

MANDATORY:
- model: `gpt-5.2-codex`
- reasoning.effort: `"xhigh"` (maximum thinking)
- max_output_tokens: 8000–16000

API key: `OPENAI_API_KEY` env var.

Transport: stdlib (`urllib.request`) unless `httpx` already present. Avoid heavy deps.

Implement:
- OpenAIResponsesClient.generate(prompt: str) -> str

───────────────────────────────────────────────────────────────────────────────
## Compile Artifacts (MUST IMPLEMENT)

Write artifacts to:

    <artifacts_dir>/<compile_hash>/compile/

compile_hash is sha256 over:
- spec file bytes
- template file bytes
- model name
- reasoning effort string

Always write:
- prompt_rendered.txt
- llm_raw_response.txt

Additionally write when applicable:
- manifest_raw.json
- manifest_normalized.json
- validation_errors.json
- compile_summary.json (timestamps, file paths, model config, compile_hash, counts)

Artifacts must be written even on failure.

───────────────────────────────────────────────────────────────────────────────
## Writing Outputs Safely

- Ensure outdir exists (create if missing).
- Respect overwrite behavior described above.
- Write each WO-*.json atomically (temp + os.replace).
- Write WORK_ORDERS_MANIFEST.json LAST.

WORK_ORDERS_MANIFEST.json must contain the normalized top-level object.

───────────────────────────────────────────────────────────────────────────────
## File Layout to Create

Add a new top-level package:

planner/
  __init__.py
  __main__.py
  cli.py
  compiler.py
  openai_client.py
  validation.py
  io.py
  prompt_template.py

Keep code minimal, explicit, and readable.

───────────────────────────────────────────────────────────────────────────────
## Non-Goals (DO NOT IMPLEMENT)

- No execution of work orders
- No LangGraph integration
- No cloud deployment
- No wrapper that invokes factory
- No tests
- No schema changes

───────────────────────────────────────────────────────────────────────────────
## Definition of Done

1) `python -m planner --help` works.
2) `python -m planner compile --spec <file> --outdir <dir>`:
   - renders prompt
   - calls GPT-5.2-Codex with max reasoning
   - parses strict JSON
   - validates against WorkOrder schema + planner rules
   - writes:
     - WORK_ORDERS_MANIFEST.json
     - WO-01.json … WO-NN.json
   - writes compile artifacts under artifacts/<compile_hash>/compile/
3) Invalid output fails cleanly with non-zero exit and written artifacts.

Implement the planner cleanly and stop.
