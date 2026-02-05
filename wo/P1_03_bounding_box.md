---
title: "P1.03 - Bounding box computation"
repo: https://github.com/teerev/lsystem-plants
clone_branch: aos/lsystem
push_branch: aos/lsystem
max_iterations: 10
context_files:
  - "src/lsystem/turtle.py"

# Quality gates
min_assertions: 5
coverage_threshold: 80
min_tests: 4
max_test_failures: 0
require_hypothesis: false
require_type_check: true
shell_policy: warn

acceptance_commands:
  - "pip install -e ."
  - "pytest tests/test_bounds.py -v"
  - "mypy src/lsystem/bounds.py --ignore-missing-imports"
---

# P1.03 - Bounding Box Computation

## Goal

Implement bounding box calculation for line segments to enable auto-scaling plants to fit any canvas size.

## Scope

### In Scope
- Create `src/lsystem/bounds.py`
- Implement `BoundingBox` dataclass with min/max coordinates
- Implement `compute_bounds(segments: list[Segment]) -> BoundingBox`
- Implement `transform_segments()` to scale and translate segments to fit a target canvas
- Add padding support for margins

### Out of Scope
- Rotation transformations
- Non-uniform scaling
- Aspect ratio preservation (keep it simple: fit to box)

## Design Constraints

- **Determinism**: Same segments = same bounding box and transformed output
- Handle edge case of empty segment list (return zero-size box or raise)
- Handle edge case of single point (all segments at same location)
- Padding specified as absolute units, not percentage
- Transformation should center the drawing in the target canvas

## Acceptance Criteria

- [ ] `BoundingBox` dataclass with `min_x`, `min_y`, `max_x`, `max_y`
- [ ] `BoundingBox.width` and `BoundingBox.height` properties
- [ ] `compute_bounds()` returns correct box for arbitrary segments
- [ ] `transform_segments()` scales and translates to fit target width/height
- [ ] Padding parameter subtracts from available canvas space
- [ ] Empty segment list handled gracefully (returns None or raises ValueError)
- [ ] Result is centered in target canvas

## Notes for Implementation

- **PROJECT LAYOUT**: Source files go in `src/lsystem/`. Imports use `from lsystem import ...` (NOT `from src.lsystem`). Tests go in `tests/`.
- Iterate all segment endpoints to find min/max x and y
- Uniform scaling: use `min(scale_x, scale_y)` to preserve aspect ratio
- Translation: shift so center of bounds maps to center of canvas
- Consider adding `BoundingBox.center` property for convenience

## Suggested Tests

- `test_single_segment_bounds`: One segment has correct min/max
- `test_multiple_segments_bounds`: Complex shape bounds computed correctly
- `test_transform_fits_canvas`: Transformed segments fit within target dimensions
- `test_transform_with_padding`: Padding reduces effective canvas size
- `test_empty_segments`: Empty list returns None or raises appropriately
- `test_aspect_ratio_preserved`: Scaling is uniform (no distortion)
- `test_centering`: Result is centered in canvas

## Public APIs

- `lsystem.bounds.BoundingBox`: Dataclass with `min_x`, `min_y`, `max_x`, `max_y`
  - `width: float` (property)
  - `height: float` (property)
  - `center: tuple[float, float]` (property)
- `lsystem.bounds.compute_bounds(segments: list[Segment]) -> BoundingBox | None`
- `lsystem.bounds.transform_segments(segments: list[Segment], width: float, height: float, padding: float = 0) -> list[Segment]`
