---
title: "TEST - Debug iteration v2"
repo: "~/repos/debug_test"
acceptance_commands:
  # Basic functionality
  - "python -c 'from calculator import evaluate; assert evaluate(\"2 + 3\") == 5'"
  - "python -c 'from calculator import evaluate; assert evaluate(\"10 - 4\") == 6'"
  - "python -c 'from calculator import evaluate; assert evaluate(\"3 * 7\") == 21'"
  - "python -c 'from calculator import evaluate; assert evaluate(\"20 / 4\") == 5'"
  # Operator precedence (likely to fail first attempt)
  - "python -c 'from calculator import evaluate; assert evaluate(\"2 + 3 * 4\") == 14, f\"got {evaluate(\"2 + 3 * 4\")}\"'"
  - "python -c 'from calculator import evaluate; assert evaluate(\"10 - 2 * 3\") == 4, f\"got {evaluate(\"10 - 2 * 3\")}\"'"
  # Parentheses (likely to fail)
  - "python -c 'from calculator import evaluate; assert evaluate(\"(2 + 3) * 4\") == 20, f\"got {evaluate(\"(2 + 3) * 4\")}\"'"
  # Negative results
  - "python -c 'from calculator import evaluate; assert evaluate(\"3 - 10\") == -7'"
  # Floating point
  - "python -c 'from calculator import evaluate; assert evaluate(\"7 / 2\") == 3.5'"
  # Whitespace handling
  - "python -c 'from calculator import evaluate; assert evaluate(\"  2+3  \") == 5'"
  - "python -c 'from calculator import evaluate; assert evaluate(\"2+3\") == 5'"
forbidden_paths: []
allowed_paths: []
env: {}
command_timeout_sec: 30
notes: ""
context_files: []
---

# TEST - Debug Iteration v2

## Goal

Create a simple arithmetic expression evaluator.

## Scope

- Create `calculator.py` with an `evaluate(expr)` function
- Function takes a string like `"2 + 3 * 4"` and returns the numeric result

## Design Constraints

- No external dependencies (stdlib only)
- Must handle operator precedence correctly (* and / before + and -)
- Must handle parentheses
