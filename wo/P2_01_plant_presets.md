---
title: "P2.01 - Plant presets library"
repo: https://github.com/teerev/lsystem-plants
clone_branch: aos/lsystem
push_branch: aos/lsystem
max_iterations: 10
context_files:
  - "src/lsystem/core.py"
  - "src/lsystem/turtle.py"

# Quality gates
min_assertions: 6
coverage_threshold: 80
min_tests: 4
max_test_failures: 0
require_hypothesis: false
require_type_check: true
shell_policy: warn

acceptance_commands:
  - "pip install -e ."
  - "pytest tests/test_presets.py -v"
  - "mypy src/lsystem/presets.py --ignore-missing-imports"
---

# P2.01 - Plant Presets Library

## Goal

Create a library of curated plant-like L-system presets that produce aesthetically pleasing botanical images.

## Scope

### In Scope
- Create `src/lsystem/presets.py`
- Implement `Preset` dataclass bundling `LSystem`, angle, step, and recommended iterations
- Provide at least 3 distinct plant presets:
  - Simple branching plant (small, fast)
  - Fern or fractal plant (medium complexity)
  - Bush or tree (larger, more detailed)
- Implement `get_preset(name: str) -> Preset`
- Implement `list_presets() -> list[str]`

### Out of Scope
- User-defined presets (they can create `LSystem` directly)
- Stochastic variations
- Seasonal/color variations

## Design Constraints

- Each preset must produce visually distinct, recognizable plant forms
- Presets should complete expansion in reasonable time (< 1 second)
- All presets use only the supported symbols: `F`, `f`, `+`, `-`, `[`, `]`, `|`
- Preset names should be descriptive: `"fern"`, `"bush"`, `"tree"`, `"weed"`, etc.
- Include recommended iteration count that balances detail vs. performance

## Acceptance Criteria

- [ ] `Preset` dataclass with `name`, `system: LSystem`, `angle`, `step`, `iterations`
- [ ] At least 3 presets available: one small, one medium, one large
- [ ] `get_preset(name)` returns preset or raises `KeyError` for unknown name
- [ ] `list_presets()` returns list of available preset names
- [ ] Each preset expands and interprets without error
- [ ] Each preset produces at least 10 segments (non-trivial output)
- [ ] Presets are documented with brief visual descriptions

## Notes for Implementation

- **PROJECT LAYOUT**: Source files go in `src/lsystem/`. Imports use `from lsystem import ...` (NOT `from src.lsystem`). Tests go in `tests/`.
- Classic L-system plants to consider:
  - Fractal plant: axiom `X`, rules `X -> F+[[X]-X]-F[-FX]+X`, `F -> FF`, angle 25°
  - Fern: axiom `X`, rules `X -> F[+X]F[-X]+X`, `F -> FF`, angle 20°
  - Bush: axiom `F`, rules `F -> FF+[+F-F-F]-[-F+F+F]`, angle 22.5°
- Store presets in a module-level dict for easy lookup
- Add docstrings describing what each plant looks like

## Suggested Tests

- `test_get_known_preset`: `get_preset("fern")` returns a Preset
- `test_get_unknown_preset`: `get_preset("unknown")` raises `KeyError`
- `test_list_presets_not_empty`: `list_presets()` returns at least 3 names
- `test_preset_expands_successfully`: Each preset can be expanded without error
- `test_preset_produces_segments`: Each preset produces non-trivial segment list
- `test_preset_iteration_limit_safe`: Recommended iterations don't exceed max

## Public APIs

- `lsystem.presets.Preset`: Dataclass
  - `name: str`
  - `system: LSystem`
  - `angle: float`
  - `step: float`
  - `iterations: int`
  - `description: str`
- `lsystem.presets.get_preset(name: str) -> Preset`
- `lsystem.presets.list_presets() -> list[str]`
