---
title: "Example: Create Calculator Module"
repo: https://github.com/teerev/dft-orch
context_files: []
acceptance_commands:
  - "python -c 'from calculator import Calculator; c = Calculator(); print(c.add(2, 3))'"
---

# Objective

Create a simple calculator module from scratch.

## Requirements

Create `calculator.py` with a `Calculator` class that has:
- `add(a, b)` - returns sum
- `subtract(a, b)` - returns difference
- `apowerb(a, b)` - returns a raised to the power of b
- `multiply(a, b)` - returns product
- `divide(a, b)` - returns quotient (raise ValueError on division by zero)

Each method should have a docstring.
