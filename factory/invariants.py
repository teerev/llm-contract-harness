"""
Factory-owned invariant checks that run independent of SE.

These checks cannot be gamed by SE because SE cannot modify this file.
The invariant harness provides an independent verification layer that runs
after acceptance commands, checking properties that the SE cannot circumvent.

This module is the foundation for Layer 2 of the 3-layer verification gate:
- Layer 1: SE-authored tests (low trust - SE controls both code and tests)
- Layer 2: Factory-owned invariants (medium trust - SE cannot modify these checks)
- Layer 3: Verifier LLM (high trust - independent adversarial review)
"""

from pathlib import Path
from typing import Any

from .schemas import InvariantResult, InvariantReport


# =============================================================================
# Individual Invariant Checks
# =============================================================================

def _check_tests_exist(workspace: Path) -> InvariantResult:
    """
    Check that test files exist in the workspace.
    
    This prevents SE from submitting code without any tests at all.
    Looks for standard pytest naming conventions: test_*.py and *_test.py
    """
    test_patterns = [
        "tests/**/test_*.py",
        "tests/**/*_test.py",
        "**/test_*.py",
        "**/*_test.py",
    ]
    
    test_files: list[Path] = []
    for pattern in test_patterns:
        test_files.extend(workspace.glob(pattern))
    
    # Deduplicate (patterns may overlap)
    test_files = list(set(test_files))
    
    if not test_files:
        return InvariantResult(
            passed=False,
            check_name="tests_exist",
            message="No test files found. Expected test_*.py or *_test.py files.",
            details={"patterns_checked": test_patterns}
        )
    
    return InvariantResult(
        passed=True,
        check_name="tests_exist",
        message=f"Found {len(test_files)} test file(s).",
        details={"test_files": [str(f.relative_to(workspace)) for f in sorted(test_files)[:10]]}
    )


# =============================================================================
# Main Invariant Runner
# =============================================================================

def run_invariants(
    workspace: Path,
    se_packet: dict[str, Any],
    work_order: dict[str, Any],
) -> InvariantReport:
    """
    Run all invariant checks against the workspace.
    
    This function is called by the TR node AFTER acceptance commands,
    providing an independent verification layer that cannot be gamed by SE.
    
    Args:
        workspace: Path to the workspace directory where SE changes were applied
        se_packet: The SE's output packet containing writes and assumptions
        work_order: The work order configuration
        
    Returns:
        InvariantReport with all_passed flag and list of individual results
    """
    results: list[InvariantResult] = []
    
    # M2: Check that test files exist
    results.append(_check_tests_exist(workspace))
    
    # Future invariant checks:
    # - M3: _check_compileall
    # - M5: _check_tests_nontrivial
    # - M6: _check_coverage
    # - M8: _check_tests_changed_with_src
    
    # Compute overall pass/fail
    all_passed = all(r.passed for r in results) if results else True
    
    return InvariantReport(all_passed=all_passed, results=results)
