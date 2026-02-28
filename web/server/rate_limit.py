"""SQLite-backed rate limiter — per-IP and global daily caps."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

from web.server import config


_lock = threading.Lock()
_DB_PATH = os.path.join(config.ARTIFACTS_DIR, ".rate_limit.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS usage ("
        "  ip   TEXT NOT NULL,"
        "  day  TEXT NOT NULL,"
        "  runs INTEGER NOT NULL DEFAULT 0,"
        "  PRIMARY KEY (ip, day)"
        ")"
    )
    conn.commit()
    return conn


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_quota(ip: str) -> dict:
    """Return current quota state without incrementing."""
    day = _today()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT runs FROM usage WHERE ip = ? AND day = ?", (ip, day)
            ).fetchone()
            ip_used = row[0] if row else 0

            row = conn.execute(
                "SELECT COALESCE(SUM(runs), 0) FROM usage WHERE day = ?", (day,)
            ).fetchone()
            global_used = row[0] if row else 0
        finally:
            conn.close()

    return {
        "ip_used": ip_used,
        "ip_limit": config.RATE_LIMIT_PER_IP,
        "ip_remaining": max(0, config.RATE_LIMIT_PER_IP - ip_used),
        "global_used": global_used,
        "global_limit": config.RATE_LIMIT_GLOBAL,
        "global_remaining": max(0, config.RATE_LIMIT_GLOBAL - global_used),
    }


def try_consume(ip: str) -> tuple[bool, dict]:
    """Attempt to consume one run. Returns (allowed, quota_state)."""
    day = _today()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT runs FROM usage WHERE ip = ? AND day = ?", (ip, day)
            ).fetchone()
            ip_used = row[0] if row else 0

            row = conn.execute(
                "SELECT COALESCE(SUM(runs), 0) FROM usage WHERE day = ?", (day,)
            ).fetchone()
            global_used = row[0] if row else 0

            if ip_used >= config.RATE_LIMIT_PER_IP:
                return False, {
                    "ip_used": ip_used,
                    "ip_limit": config.RATE_LIMIT_PER_IP,
                    "ip_remaining": 0,
                    "global_used": global_used,
                    "global_limit": config.RATE_LIMIT_GLOBAL,
                    "global_remaining": max(0, config.RATE_LIMIT_GLOBAL - global_used),
                    "reason": "ip",
                }

            if global_used >= config.RATE_LIMIT_GLOBAL:
                return False, {
                    "ip_used": ip_used,
                    "ip_limit": config.RATE_LIMIT_PER_IP,
                    "ip_remaining": max(0, config.RATE_LIMIT_PER_IP - ip_used),
                    "global_used": global_used,
                    "global_limit": config.RATE_LIMIT_GLOBAL,
                    "global_remaining": 0,
                    "reason": "global",
                }

            conn.execute(
                "INSERT INTO usage (ip, day, runs) VALUES (?, ?, 1) "
                "ON CONFLICT (ip, day) DO UPDATE SET runs = runs + 1",
                (ip, day),
            )
            conn.commit()

            return True, {
                "ip_used": ip_used + 1,
                "ip_limit": config.RATE_LIMIT_PER_IP,
                "ip_remaining": max(0, config.RATE_LIMIT_PER_IP - ip_used - 1),
                "global_used": global_used + 1,
                "global_limit": config.RATE_LIMIT_GLOBAL,
                "global_remaining": max(0, config.RATE_LIMIT_GLOBAL - global_used - 1),
            }
        finally:
            conn.close()
