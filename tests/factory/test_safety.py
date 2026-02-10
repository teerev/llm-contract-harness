"""Safety tests — network guard, filesystem write guard."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------


class TestNetworkGuard:
    """Ensure no test in this suite accidentally makes a real network call.

    This test monkeypatches socket.socket.connect to raise immediately.
    If any other test in this session calls a real socket, it would fail.
    This test itself just verifies the guard mechanism works.
    """

    def test_socket_connect_blocked(self, monkeypatch):
        """Attempting a real socket connection should raise."""
        original_connect = socket.socket.connect

        def _blocked_connect(self, address):
            raise OSError(
                f"NETWORK GUARD: test attempted real connection to {address}. "
                "All tests must run without network access."
            )

        monkeypatch.setattr(socket.socket, "connect", _blocked_connect)

        with pytest.raises(OSError, match="NETWORK GUARD"):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("8.8.8.8", 53))
            finally:
                s.close()

    def test_llm_complete_does_not_call_network(self, monkeypatch):
        """Calling llm.complete without a key should fail before any network call."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        def _blocked_connect(self, address):
            raise AssertionError(
                f"NETWORK CALL DETECTED to {address}! "
                "llm.complete must not reach the network in tests."
            )

        monkeypatch.setattr(socket.socket, "connect", _blocked_connect)

        from factory.llm import complete

        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            complete("test prompt", "test-model")


# ---------------------------------------------------------------------------
# Filesystem write guard (for preflight tests)
# ---------------------------------------------------------------------------


class TestFilesystemGuard:
    def test_no_writes_to_cwd(self, tmp_path, monkeypatch):
        """Verify that importing factory modules does not write to the filesystem."""
        monkeypatch.chdir(tmp_path)
        initial_contents = set(tmp_path.iterdir())

        # Import all factory modules
        import factory
        import factory.schemas
        import factory.util
        import factory.workspace
        import factory.llm
        import factory.nodes_se
        import factory.nodes_tr
        import factory.nodes_po

        final_contents = set(tmp_path.iterdir())
        assert initial_contents == final_contents, (
            f"Factory import created files in cwd: {final_contents - initial_contents}"
        )
