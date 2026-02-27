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
    uvicorn.run(
        "web.server.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=True,
        reload_excludes=["artifacts/*", "my-project/*", "wo/*", "runs/*"],
    )


if __name__ == "__main__":
    main()
