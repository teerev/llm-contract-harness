"""DynamoDB-backed RunStore implementation.

Activated when ``LLMCH_DYNAMO_TABLE`` is set.  Uses a single DynamoDB
table with ``run_id`` as the partition key.

Events and artifacts remain file-based (Phase 2 keeps local FS for the
pipeline working directory).  Only run *metadata* moves to DynamoDB.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from web.server import config
from web.server.interfaces import RunMeta, RunOptions

from shared.run_context import generate_ulid

DYNAMO_TABLE: str = os.environ.get("LLMCH_DYNAMO_TABLE", "").strip()


def _client():  # noqa: ANN202
    import boto3
    return boto3.resource("dynamodb").Table(DYNAMO_TABLE)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DynamoRunStore:
    """Persists RunMeta as a DynamoDB item keyed by ``run_id``.

    ``events_path()`` still returns a local filesystem path — the
    EventLog and SSE tailing remain file-based in this phase.
    """

    def __init__(self, artifacts_dir: str | None = None) -> None:
        self._artifacts_dir = artifacts_dir or config.ARTIFACTS_DIR

    def _run_dir(self, run_id: str) -> str:
        return os.path.join(self._artifacts_dir, "pipeline", run_id)

    def create(self, prompt: str, opts: RunOptions) -> str:
        run_id = generate_ulid()

        # Still create the local pipeline directory for events.jsonl and repo
        run_dir = self._run_dir(run_id)
        os.makedirs(run_dir, exist_ok=True)

        now = _ts()
        item: dict[str, Any] = {
            "run_id": run_id,
            "status": "queued",
            "prompt": prompt,
            "started_at": now,
            "updated_at": now,
            "work_order_count": 0,
            "work_order_verdicts": {},
            "factory_run_ids": [],
            "opts": {
                "push_to_demo": opts.push_to_demo,
                "branch_name": opts.branch_name,
            },
        }
        _client().put_item(Item=_serialize(item))
        return run_id

    def get(self, run_id: str) -> RunMeta:
        resp = _client().get_item(Key={"run_id": run_id})
        item = resp.get("Item")
        if not item:
            raise FileNotFoundError(f"Run not found: {run_id}")
        return _item_to_meta(item)

    def update(self, run_id: str, **fields: Any) -> None:
        fields["updated_at"] = _ts()
        expr_parts: list[str] = []
        names: dict[str, str] = {}
        values: dict[str, Any] = {}

        for i, (k, v) in enumerate(fields.items()):
            alias = f"#f{i}"
            val_alias = f":v{i}"
            names[alias] = k
            values[val_alias] = _dynamo_value(v)
            expr_parts.append(f"{alias} = {val_alias}")

        _client().update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def events_path(self, run_id: str) -> str:
        return os.path.join(self._run_dir(run_id), "events.jsonl")


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize(item: dict) -> dict:
    """Prepare a dict for DynamoDB — convert None/empty values."""
    out: dict[str, Any] = {}
    for k, v in item.items():
        out[k] = _dynamo_value(v)
    return out


def _dynamo_value(v: Any) -> Any:
    """Convert a Python value to a DynamoDB-compatible value."""
    if v is None:
        return "__NULL__"
    if isinstance(v, dict):
        return {dk: _dynamo_value(dv) for dk, dv in v.items()} if v else {"__EMPTY_MAP__": True}
    if isinstance(v, list):
        return v if v else ["__EMPTY_LIST__"]
    return v


def _item_to_meta(item: dict) -> RunMeta:
    """Convert a DynamoDB item back to a RunMeta dataclass."""
    def _restore(v: Any) -> Any:
        if v == "__NULL__":
            return None
        if isinstance(v, dict):
            if "__EMPTY_MAP__" in v:
                return {}
            return {dk: _restore(dv) for dk, dv in v.items()}
        if isinstance(v, list):
            if v == ["__EMPTY_LIST__"]:
                return []
            return [_restore(x) for x in v]
        return v

    item = {k: _restore(v) for k, v in item.items()}

    opts_raw = item.pop("opts", {})
    opts = RunOptions(**opts_raw) if isinstance(opts_raw, dict) else RunOptions()

    # Map DynamoDB field names to RunMeta field names
    item.pop("updated_at", None)
    run_id = item.pop("run_id", "")

    # Handle push_result if present (legacy or from to_dict serialization)
    push_result = item.pop("push_result", None)
    if push_result and isinstance(push_result, dict):
        item.setdefault("push_remote", push_result.get("remote"))
        item.setdefault("push_branch", push_result.get("branch"))
        item.setdefault("push_commit_sha", push_result.get("commit_sha"))
        item.setdefault("push_url", push_result.get("url"))

    # DynamoDB returns Decimal for numbers — convert to int
    for int_field in ("work_order_count",):
        if int_field in item and not isinstance(item[int_field], int):
            item[int_field] = int(item[int_field])

    return RunMeta(pipeline_run_id=run_id, opts=opts, **item)
