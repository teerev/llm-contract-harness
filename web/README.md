# llmch Web UI — Development Setup

## Prerequisites

- Python 3.11+
- Node.js 20+ and npm
- The repo installed in editable mode: `pip install -e ".[web]"`

## Quick start (two terminals)

### Terminal 1 — Backend (FastAPI on :8000)

```bash
python -m web.server.main
```

### Terminal 2 — Frontend (Vite dev server on :5173)

```bash
cd web/ui
npm install
npm run dev
```

Open **http://localhost:5173** in your browser. The Vite dev server
proxies `/api` requests to the backend at :8000.

## Production build

```bash
cd web/ui
npm run build          # outputs to web/ui/dist/
```

Then run only the backend — it serves the built UI from `web/ui/dist/`
automatically:

```bash
python -m web.server.main
# open http://localhost:8000
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLMCH_HOST` | `127.0.0.1` | Backend bind address |
| `LLMCH_PORT` | `8000` | Backend bind port |
| `LLMCH_RUNS_DIR` | `./runs` | Per-run data directory |
| `LLMCH_ARTIFACTS_DIR` | `./artifacts` | Artifacts root (shared with CLI) |
| `LLMCH_DEMO_REMOTE_URL` | *(none)* | Git remote for demo push |
| `OPENAI_API_KEY` | *(required)* | OpenAI API key |
