"""API route handlers — wired to FileStore / RunStore / Runner instances."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from web.server.interfaces import VALID_ROOTS
from web.server.rate_limit import check_quota, try_consume
from web.server.sse import stream_events
from web.server.store_local import MAX_FILE_READ_BYTES

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateRunRequest(BaseModel):
    prompt: str
    push_to_demo: bool = False


# ---------------------------------------------------------------------------
# Dependency accessors (set at app startup via init_routes)
# ---------------------------------------------------------------------------

_run_store = None
_file_store = None
_runner = None


def init_routes(run_store, file_store, runner) -> None:  # noqa: ANN001
    global _run_store, _file_store, _runner
    _run_store = run_store
    _file_store = file_store
    _runner = runner


# ---------------------------------------------------------------------------
# POST /runs
# ---------------------------------------------------------------------------

@router.get("/quota")
async def get_quota(request: Request):
    ip = request.client.host if request.client else "unknown"
    return check_quota(ip)


@router.post("/runs")
async def create_run(body: CreateRunRequest, request: Request) -> JSONResponse:
    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required")

    ip = request.client.host if request.client else "unknown"
    allowed, quota = try_consume(ip)
    if not allowed:
        reason = quota.get("reason", "")
        if reason == "ip":
            msg = (
                f"Rate limit reached: you have used all {quota['ip_limit']} "
                f"runs allowed per day. Try again tomorrow."
            )
        else:
            msg = (
                f"Global daily limit reached: {quota['global_limit']} total runs "
                f"have been used today. Try again tomorrow."
            )
        return JSONResponse(
            {"error": msg, "quota": quota},
            status_code=429,
        )

    import secrets
    from web.server.interfaces import RunOptions

    branch_name = None
    if body.push_to_demo:
        suffix = secrets.token_hex(3)
        branch_name = f"build-{suffix}"

    opts = RunOptions(
        push_to_demo=body.push_to_demo,
        branch_name=branch_name,
    )
    run_id = _run_store.create(body.prompt.strip(), opts)
    _runner.start(run_id, body.prompt.strip(), opts)
    return JSONResponse(
        {"run_id": run_id, "branch_name": branch_name, "quota": quota},
        status_code=202,
    )


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    try:
        meta = _run_store.get(run_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Run not found: {run_id}")
    return meta.to_dict()


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/events  (SSE)
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}/events")
async def get_events(run_id: str, last_seq: int = 0):
    try:
        _run_store.get(run_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Run not found: {run_id}")

    events_path = _run_store.events_path(run_id)

    return StreamingResponse(
        stream_events(events_path, last_seq=last_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/tree?root=...
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}/tree")
async def get_tree(
    run_id: str,
    root: str = Query(..., description="One of: work_orders, artifacts, repo"),
):
    if root not in VALID_ROOTS:
        raise HTTPException(400, f"Invalid root: {root!r}. Must be one of {sorted(VALID_ROOTS)}")
    try:
        _run_store.get(run_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Run not found: {run_id}")

    entries = _file_store.tree(run_id, root)
    return {"root": root, "entries": [e.to_dict() for e in entries]}


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/file?root=...&path=...
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}/file")
async def get_file(
    run_id: str,
    root: str = Query(...),
    path: str = Query(...),
):
    if root not in VALID_ROOTS:
        raise HTTPException(400, f"Invalid root: {root!r}")
    try:
        _run_store.get(run_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Run not found: {run_id}")

    try:
        data = _file_store.read(run_id, root, path)
    except FileNotFoundError:
        raise HTTPException(404, f"File not found: {root}/{path}")
    except PermissionError:
        raise HTTPException(403, "Path escapes root")

    size = len(data)
    truncated = size >= MAX_FILE_READ_BYTES
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        content = data.decode("latin-1")

    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

    return {
        "path": path,
        "content": content,
        "size": size,
        "line_count": line_count,
        "truncated": truncated,
    }
