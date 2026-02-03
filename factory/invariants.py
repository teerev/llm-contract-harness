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
    
    # Invariant checks will be added in subsequent milestones:
    # - M2: _check_tests_exist
    # - M3: _check_compileall
    # - M5: _check_tests_nontrivial
    # - M6: _check_coverage
    # - M8: _check_tests_changed_with_src
    
    # Compute overall pass/fail
    all_passed = all(r.passed for r in results) if results else True
    
    return InvariantReport(all_passed=all_passed, results=results)
