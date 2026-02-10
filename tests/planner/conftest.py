"""Shared fixtures for planner tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Hard-block any real HTTP from planner tests.

    Ensures no test accidentally makes network calls even if mocking
    is incorrectly applied or removed.  Any attempt to create an
    httpx.Client will raise immediately.
    """
    import httpx

    def _boom(*args, **kwargs):
        raise RuntimeError(
            "Network call attempted in planner test! "
            "All HTTP must be mocked."
        )

    monkeypatch.setattr(httpx, "Client", _boom)
    monkeypatch.setattr(httpx, "AsyncClient", _boom)
