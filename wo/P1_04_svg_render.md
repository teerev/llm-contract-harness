---
title: "P1.04 - SVG rendering with canonical output"
repo: https://github.com/teerev/lsystem-plants
clone_branch: aos/lsystem
push_branch: aos/lsystem
max_iterations: 10
context_files:
  - "src/lsystem/turtle.py"
  - "src/lsystem/bounds.py"

# Quality gates
min_assertions: 5
coverage_threshold: 80
min_tests: 5
max_test_failures: 0
require_hypothesis: false
require_type_check: true
shell_policy: warn

acceptance_commands:
  - "pip install -e ."
  - "pytest tests/test_svg.py -v"
  - "mypy src/lsystem/render_svg.py --ignore-missing-imports"
---

# P1.04 - SVG Rendering with Canonical Output

## Goal

Implement SVG rendering for line segments with a canonical serialization format that guarantees deterministic output for testing.

## Scope

### In Scope
- Create `src/lsystem/render_svg.py`
- Implement `render_svg(segments: list[Segment], width: int, height: int, ...) -> str`
- Implement `save_svg(svg_content: str, path: Path) -> None`
- Define canonical SVG format with stable ordering and float formatting
- Support configurable stroke color and width
- Auto-scale segments to fit canvas (using bounds module)

### Out of Scope
- PNG export (separate work order)
- Gradients, fills, or complex styling
- Animation
- Multiple colors per segment

## Design Constraints

- **Canonical output is critical for deterministic testing**:
  - Fixed float precision (4 decimal places)
  - Consistent whitespace (single spaces, newlines in predictable places)
  - Stable attribute ordering in SVG elements
  - No random IDs or timestamps in output
- Use only Python stdlib (no external SVG libraries)
- SVG should be valid and render correctly in browsers

## Acceptance Criteria

- [ ] `render_svg()` returns valid SVG string
- [ ] Same segments always produce byte-identical SVG output
- [ ] Float values formatted to exactly 4 decimal places
- [ ] SVG attributes in consistent alphabetical order
- [ ] Canvas size (width/height) configurable
- [ ] Stroke color and width configurable with sensible defaults
- [ ] `save_svg()` writes string to file at given path
- [ ] Empty segment list produces valid empty SVG (just the container)

## Notes for Implementation

- **PROJECT LAYOUT**: Source files go in `src/lsystem/`. Imports use `from lsystem import ...` (NOT `from src.lsystem`). Tests go in `tests/`.
- Use f-strings with explicit format specifiers: `f"{value:.4f}"`
- SVG structure: `<svg>` container with `<line>` elements (or single `<path>` with move/line commands)
- Consider `<path d="M x1 y1 L x2 y2 ...">` for efficiency (fewer elements)
- Default stroke: `#228B22` (forest green), width: 1
- Default canvas: 800x600 pixels
- Call `transform_segments()` from bounds module before rendering

## Suggested Tests

- `test_svg_valid_structure`: Output starts with `<svg` and ends with `</svg>`
- `test_svg_deterministic`: Same segments produce identical SVG strings
- `test_float_precision`: Coordinates formatted to exactly 4 decimal places
- `test_custom_stroke`: Stroke color and width reflected in output
- `test_segments_rendered`: Each segment appears in SVG output
- `test_save_creates_file`: `save_svg()` creates file with correct content
- `test_empty_segments`: Empty list produces valid minimal SVG

## Public APIs

- `lsystem.render_svg.render_svg(segments: list[Segment], width: int = 800, height: int = 600, stroke: str = "#228B22", stroke_width: float = 1.0, padding: float = 20.0) -> str`
- `lsystem.render_svg.save_svg(content: str, path: Path) -> None`
