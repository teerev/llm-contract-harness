---
title: "P1.02 - Turtle graphics interpreter"
repo: "~/repos/lsystem-plants"
acceptance_commands:
  - "pip install -e ."
  - "pytest tests/test_turtle.py -v"
  - "mypy src/lsystem/turtle.py --ignore-missing-imports"
---

# P1.02 - Turtle Graphics Interpreter

## Goal

Implement a turtle graphics interpreter that converts an L-system instruction string into a list of 2D line segments.

## Scope

### In Scope
- Create `src/lsystem/turtle.py`
- Implement `TurtleState` dataclass (position, angle)
- Implement `interpret(instructions: str, angle: float, step: float) -> list[Segment]`
- Support standard plant L-system symbols:
  - `F`: move forward and draw line
  - `f`: move forward without drawing
  - `+`: turn left by angle
  - `-`: turn right by angle
  - `[`: push current state onto stack
  - `]`: pop state from stack
  - `|`: turn 180 degrees (optional but useful)
- Return list of line segments as `Segment` named tuples

### Out of Scope
- 3D turtle graphics
- Variable step lengths
- Color/thickness changes
- Rendering to any format (next work order)

## Design Constraints

- **Determinism**: Same input string + same angle + same step = identical segments list
- Angles in degrees (more intuitive for users)
- Initial turtle state: position (0, 0), heading up (90 degrees from positive X-axis)
- Stack operations must be balanced (warn or handle unbalanced gracefully)
- Segment: `((x1, y1), (x2, y2))` as tuple or named tuple
- Unknown symbols are silently ignored (allows decorative characters)

## Acceptance Criteria

- [ ] `TurtleState` dataclass with `x: float`, `y: float`, `angle: float`
- [ ] `Segment` named tuple with `start: tuple[float, float]`, `end: tuple[float, float]`
- [ ] `interpret()` correctly processes `F`, `f`, `+`, `-`, `[`, `]`, `|`
- [ ] `F` produces a segment; `f` does not
- [ ] `+` turns left (counter-clockwise), `-` turns right (clockwise)
- [ ] Stack push/pop correctly saves/restores position and angle
- [ ] Empty instruction string returns empty list
- [ ] Unbalanced `]` (pop from empty stack) raises `ValueError` or is handled gracefully

## Notes for Implementation

- **PROJECT LAYOUT**: Source files go in `src/lsystem/`. Imports use `from lsystem import ...` (NOT `from src.lsystem`). Tests go in `tests/`.
- Use `math.radians()` for angle conversion in trig calculations
- Heading: 0 degrees = right (+X), 90 degrees = up (+Y); start at 90
- Use a list as stack for `[` and `]` operations
- Consider `@dataclass` for `TurtleState` and `NamedTuple` for `Segment`
- Round floats to reasonable precision (e.g., 6 decimal places) for stability

## Suggested Tests

- `test_forward_draw`: `F` produces one segment from (0,0) to (0, step)
- `test_forward_no_draw`: `f` moves position but produces no segment
- `test_turn_left`: `+F` at 90° base + 90° turn draws segment to the left
- `test_turn_right`: `-F` at 90° base - 90° turn draws segment to the right
- `test_push_pop`: `[F]F` returns to original position for second F
- `test_turn_180`: `|F` reverses direction
- `test_complex_sequence`: `F[+F][-F]F` produces expected 4 segments
- `test_determinism`: Same input twice produces identical segment lists
- `test_empty_string`: Empty input returns empty list
- `test_unbalanced_pop`: Handles `]` with empty stack appropriately

## Public APIs

- `lsystem.turtle.TurtleState`: Dataclass with `x`, `y`, `angle`
- `lsystem.turtle.Segment`: NamedTuple with `start`, `end`
- `lsystem.turtle.interpret(instructions: str, angle: float, step: float) -> list[Segment]`
