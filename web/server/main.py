"""FastAPI application — serves the API and (optionally) the built UI."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from web.server import config

app = FastAPI(title="llmch", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# --- Placeholder routes (replaced in WP1) ---


@app.post("/api/v1/runs")
async def create_run() -> JSONResponse:
    return JSONResponse({"error": "not implemented"}, status_code=501)


@app.get("/api/v1/runs/{run_id}")
async def get_run(run_id: str) -> JSONResponse:
    return JSONResponse({"error": "not implemented"}, status_code=501)


@app.get("/api/v1/runs/{run_id}/events")
async def get_events(run_id: str) -> JSONResponse:
    return JSONResponse({"error": "not implemented"}, status_code=501)


@app.get("/api/v1/runs/{run_id}/tree")
async def get_tree(run_id: str, root: str = "repo") -> JSONResponse:
    return JSONResponse({"error": "not implemented"}, status_code=501)


@app.get("/api/v1/runs/{run_id}/file")
async def get_file(run_id: str, root: str = "repo", path: str = "") -> JSONResponse:
    return JSONResponse({"error": "not implemented"}, status_code=501)


if config.STATIC_DIR:
    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="ui")


def main() -> None:
    uvicorn.run(
        "web.server.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=True,
    )


if __name__ == "__main__":
    main()
