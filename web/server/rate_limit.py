"""Rate limiter — per-IP and global daily caps.

Uses SQLite by default (single-instance).  When ``LLMCH_DYNAMO_TABLE``
is set, uses DynamoDB for cross-instance shared quotas via atomic
``UpdateItem`` with ``ADD``.

Public API (unchanged):
  check_quota(ip) -> dict
  try_consume(ip) -> (bool, dict)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

from web.server import config

logger = logging.getLogger(__name__)

_DYNAMO_TABLE: str = os.environ.get("LLMCH_DYNAMO_TABLE", "").strip()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public API — dispatches to SQLite or DynamoDB backend
# ---------------------------------------------------------------------------

def check_quota(ip: str) -> dict:
    """Return current quota state without incrementing."""
    if _DYNAMO_TABLE:
        return _dynamo_check_quota(ip)
    return _sqlite_check_quota(ip)


def try_consume(ip: str) -> tuple[bool, dict]:
    """Attempt to consume one run. Returns (allowed, quota_state)."""
    if _DYNAMO_TABLE:
        return _dynamo_try_consume(ip)
    return _sqlite_try_consume(ip)


# ---------------------------------------------------------------------------
# Helpers — build quota response dict
# ---------------------------------------------------------------------------

def _quota_dict(
    ip_used: int, global_used: int, *, reason: str | None = None
) -> dict:
    d: dict = {
        "ip_used": ip_used,
        "ip_limit": config.RATE_LIMIT_PER_IP,
        "ip_remaining": max(0, config.RATE_LIMIT_PER_IP - ip_used),
        "global_used": global_used,
        "global_limit": config.RATE_LIMIT_GLOBAL,
        "global_remaining": max(0, config.RATE_LIMIT_GLOBAL - global_used),
    }
    if reason:
        d["reason"] = reason
    return d


# ═══════════════════════════════════════════════════════════════════════
# SQLite backend (default — single-instance)
# ═══════════════════════════════════════════════════════════════════════

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


def _sqlite_read_counts(conn: sqlite3.Connection, ip: str, day: str) -> tuple[int, int]:
    row = conn.execute(
        "SELECT runs FROM usage WHERE ip = ? AND day = ?", (ip, day)
    ).fetchone()
    ip_used = row[0] if row else 0
    row = conn.execute(
        "SELECT COALESCE(SUM(runs), 0) FROM usage WHERE day = ?", (day,)
    ).fetchone()
    global_used = row[0] if row else 0
    return ip_used, global_used


def _sqlite_check_quota(ip: str) -> dict:
    day = _today()
    with _lock:
        conn = _connect()
        try:
            ip_used, global_used = _sqlite_read_counts(conn, ip, day)
        finally:
            conn.close()
    return _quota_dict(ip_used, global_used)


def _sqlite_try_consume(ip: str) -> tuple[bool, dict]:
    day = _today()
    with _lock:
        conn = _connect()
        try:
            ip_used, global_used = _sqlite_read_counts(conn, ip, day)

            if ip_used >= config.RATE_LIMIT_PER_IP:
                return False, _quota_dict(ip_used, global_used, reason="ip")
            if global_used >= config.RATE_LIMIT_GLOBAL:
                return False, _quota_dict(ip_used, global_used, reason="global")

            conn.execute(
                "INSERT INTO usage (ip, day, runs) VALUES (?, ?, 1) "
                "ON CONFLICT (ip, day) DO UPDATE SET runs = runs + 1",
                (ip, day),
            )
            conn.commit()
            return True, _quota_dict(ip_used + 1, global_used + 1)
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════
# DynamoDB backend (multi-instance)
# ═══════════════════════════════════════════════════════════════════════
#
# Key schema (coexists in the same table as run metadata):
#   PK = "RATE#ip#{ip}#day#{YYYY-MM-DD}"   → per-IP counter
#   PK = "RATE#global#day#{YYYY-MM-DD}"    → global counter
#
# Uses atomic ADD to increment counters and ConditionExpression to
# enforce limits in a single round-trip.
# TTL auto-expires items after 2 days.
# ═══════════════════════════════════════════════════════════════════════

_RATE_PREFIX = "RATE#"
_TTL_SECONDS = 2 * 86400  # 2 days


def _dynamo_table():  # noqa: ANN202
    import boto3
    return boto3.resource("dynamodb").Table(_DYNAMO_TABLE)


def _ip_key(ip: str, day: str) -> str:
    return f"{_RATE_PREFIX}ip#{ip}#day#{day}"


def _global_key(day: str) -> str:
    return f"{_RATE_PREFIX}global#day#{day}"


def _ttl_epoch() -> int:
    import time
    return int(time.time()) + _TTL_SECONDS


def _dynamo_get_count(table, pk: str) -> int:  # noqa: ANN001
    resp = table.get_item(
        Key={"run_id": pk},
        ProjectionExpression="runs",
        ConsistentRead=True,
    )
    item = resp.get("Item")
    return int(item["runs"]) if item else 0


def _dynamo_check_quota(ip: str) -> dict:
    day = _today()
    try:
        table = _dynamo_table()
        ip_used = _dynamo_get_count(table, _ip_key(ip, day))
        global_used = _dynamo_get_count(table, _global_key(day))
        return _quota_dict(ip_used, global_used)
    except Exception:
        logger.exception("DynamoDB check_quota failed — falling back to permissive")
        return _quota_dict(0, 0)


def _dynamo_try_consume(ip: str) -> tuple[bool, dict]:
    day = _today()
    try:
        table = _dynamo_table()
        ip_used = _dynamo_get_count(table, _ip_key(ip, day))
        global_used = _dynamo_get_count(table, _global_key(day))

        if ip_used >= config.RATE_LIMIT_PER_IP:
            return False, _quota_dict(ip_used, global_used, reason="ip")
        if global_used >= config.RATE_LIMIT_GLOBAL:
            return False, _quota_dict(ip_used, global_used, reason="global")

        ttl = _ttl_epoch()

        # Atomically increment both counters
        table.update_item(
            Key={"run_id": _ip_key(ip, day)},
            UpdateExpression="ADD runs :one SET #t = :ttl",
            ExpressionAttributeNames={"#t": "ttl"},
            ExpressionAttributeValues={":one": 1, ":ttl": ttl},
        )
        table.update_item(
            Key={"run_id": _global_key(day)},
            UpdateExpression="ADD runs :one SET #t = :ttl",
            ExpressionAttributeNames={"#t": "ttl"},
            ExpressionAttributeValues={":one": 1, ":ttl": ttl},
        )

        return True, _quota_dict(ip_used + 1, global_used + 1)

    except Exception:
        logger.exception("DynamoDB try_consume failed — rejecting request for safety")
        return False, _quota_dict(0, 0, reason="global")
