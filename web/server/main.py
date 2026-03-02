"""FastAPI application — serves the API and (optionally) the built UI."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from web.server import config
from web.server.routes import init_routes, router
from web.server.runner_local import LocalRunner
from web.server.store_local import LocalFileStore, LocalRunStore

app = FastAPI(title="llmch", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Wire dependencies ---
# Use DynamoDB for run metadata when configured, otherwise local JSON files.
from web.server.store_dynamo import DYNAMO_TABLE
if DYNAMO_TABLE:
    from web.server.store_dynamo import DynamoRunStore
    _run_store = DynamoRunStore()
else:
    _run_store = LocalRunStore()
_file_store = LocalFileStore(run_store=_run_store)
_runner = LocalRunner(run_store=_run_store)
init_routes(_run_store, _file_store, _runner)

app.include_router(router)


@app.get("/api/v1/health")
async def health() -> dict:
    return {
        "status": "ok",
        "demo_remote_configured": bool(config.DEMO_REMOTE_URL),
    }


@app.get("/api/v1/config")
async def get_config() -> dict:
    return {
        "demo_remote_configured": bool(config.DEMO_REMOTE_URL),
    }


if config.STATIC_DIR:
    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="ui")


def main() -> None:
    """Entry point for local development (with hot-reload)."""
    uvicorn.run(
        "web.server.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=True,
        reload_excludes=["artifacts/*", "my-project/*", "wo/*", "runs/*"],
    )


def serve() -> None:
    """Entry point for production (no reload, single worker)."""
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
