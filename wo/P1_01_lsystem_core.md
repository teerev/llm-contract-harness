---
title: "P1.01 - L-system core model and expansion"
repo: "~/repos/lsystem-plants"
acceptance_commands:
  - "pip install -e ."
  - "pytest tests/test_core.py -v"
  - "mypy src/lsystem/core.py --ignore-missing-imports"
---

# P1.01 - L-system Core Model and Expansion

## Goal

Implement the core L-system data model and deterministic string expansion algorithm.

## Scope

### In Scope
- Create `src/lsystem/core.py`
- Implement `LSystem` dataclass with axiom, rules, and iteration count
- Implement `expand()` function that applies rules iteratively
- Validate inputs (non-empty axiom, valid rules dict, reasonable iteration count)
- Add safety limits to prevent string explosion (max iterations, max string length)

### Out of Scope
- Stochastic/probabilistic rules
- Parametric L-systems
- Turtle interpretation (next work order)
- Context-sensitive rules

## Design Constraints

- **Determinism is critical**: Same `LSystem` + same iterations = identical output string
- Rules mapping: `dict[str, str]` where key is single character
- Characters not in rules pass through unchanged
- Expansion is purely functional (no side effects)
- Max iterations capped at 15 to prevent runaway expansion
- Max output string length capped at 10 million characters

## Acceptance Criteria

- [ ] `LSystem` dataclass with `axiom: str`, `rules: dict[str, str]`
- [ ] `expand(system: LSystem, iterations: int) -> str` returns expanded string
- [ ] Characters not in rules are preserved unchanged
- [ ] Expansion is deterministic (same inputs = same outputs, always)
- [ ] `ValueError` raised for iterations > 15 or output exceeding max length
- [ ] `ValueError` raised for empty axiom or empty rules dict
- [ ] All public functions have docstrings with examples

## Notes for Implementation

- **PROJECT LAYOUT**: Source files go in `src/lsystem/`. Imports use `from lsystem import ...` (NOT `from src.lsystem`). Tests go in `tests/`.
- Use `@dataclass(frozen=True)` for `LSystem` to enforce immutability
- Expansion algorithm: for each iteration, replace each char using rules.get(char, char)
- Consider using `str.translate()` or simple loop (profile if needed, but don't over-optimize)
- Raise early with helpful error messages

## Suggested Tests

- `test_simple_expansion`: `F -> FF` with 1 iteration produces `FF`
- `test_multi_iteration`: `F -> FF` with 3 iterations produces `FFFFFFFF` (2^3 F's)
- `test_unknown_chars_preserved`: `A -> AB` on `XAX` produces `XABX`
- `test_determinism`: Same system expanded twice produces identical strings
- `test_max_iterations_enforced`: iterations=20 raises `ValueError`
- `test_max_length_enforced`: Expansion that would exceed limit raises error
- `test_empty_axiom_rejected`: Empty string axiom raises `ValueError`
- `test_complex_rules`: Multiple rules applied correctly in single pass

## Public APIs

- `lsystem.core.LSystem`: Frozen dataclass
  - `axiom: str`
  - `rules: dict[str, str]`
- `lsystem.core.expand(system: LSystem, iterations: int) -> str`
