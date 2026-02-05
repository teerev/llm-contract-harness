---
title: "TEST - Debug iteration test"
repo: "~/repos/debug_test"
acceptance_commands:
  - "python -c 'from slugify import slugify; r=slugify(\"Hello World\"); assert r==\"hello-world\", f\"Expected hello-world, got {r!r}\"'"
  - "python -c 'from slugify import slugify; r=slugify(\"  Hello   World  \"); assert r==\"hello-world\", f\"Expected hello-world, got {r!r}\"'"
  - "python -c 'from slugify import slugify; r=slugify(\"Hello---World\"); assert r==\"hello-world\", f\"Expected hello-world, got {r!r}\"'"
  - "python -c 'from slugify import slugify; r=slugify(\"--Hello--World--\"); assert r==\"hello-world\", f\"Expected hello-world, got {r!r}\"'"
  - "python -c 'from slugify import slugify; r=slugify(\"Hello @#$% World!\"); assert r==\"hello-world\", f\"Expected hello-world, got {r!r}\"'"
  - "python -c 'from slugify import slugify; r=slugify(\"café résumé\"); assert r==\"caf-rsum\", f\"Expected caf-rsum, got {r!r}\"'"
  - "python -c 'from slugify import slugify; r=slugify(\"\"); assert r==\"\", f\"Expected empty string, got {r!r}\"'"
  - "python -c 'from slugify import slugify; r=slugify(\"---\"); assert r==\"\", f\"Expected empty string, got {r!r}\"'"
  - "python -c 'from slugify import slugify; r=slugify(\"ABC123xyz\"); assert r==\"abc123xyz\", f\"Expected abc123xyz, got {r!r}\"'"
forbidden_paths: []
allowed_paths: []
env: {}
command_timeout_sec: 30
notes: ""
context_files: ["dist.py"]
---

# TEST - Debug Iteration Test

## Goal

Create a URL slug generator.

## Scope

- Create `slugify.py` with a `slugify(text)` function

## Design Constraints

- No external dependencies (stdlib only)
