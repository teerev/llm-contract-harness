# AOS - Agent Orchestration Service

An agentic code generation service that takes work orders (task descriptions) and produces working code. Built on LangGraph with a SE → TR → PO loop (Software Engineer → Tool Runner → Product Owner).

## Quick Start

```bash
# 1. Install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Set up API keys
mv .env.example .env #  Edit .env with your OPENAI_API_KEY and GITHUB_TOKEN

# 3. Start everything
source .env
docker compose up -d      # Postgres + Redis (background)
alembic upgrade head      # Create or update existing database tables
honcho start              # API + Worker (foreground, color-coded logs)
```

That's it — one terminal, one command (`honcho start`) for the API and worker.

To stop: `Ctrl+C`

## Submitting Work Orders

Work orders are self-contained markdown files with YAML frontmatter. **The work order is the single source of truth** - all configuration is in the file, not CLI flags.

```markdown
---
title: Add calculator module
repo: https://github.com/user/repo
clone_branch: main
push_branch: aos/calculator-feature
max_iterations: 5
acceptance_commands:
  - python -c "from calculator import add; assert add(2,3) == 5"
allowed_paths:
  - "*.py"
---

Create a calculator.py module with an `add(a, b)` function.
```

### Work Order Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `repo` | **Yes** | — | GitHub URL of the repository to work on |
| `clone_branch` | No | `main` | Branch or SHA to clone from |
| `push_branch` | No | — | Branch to push results to (if set, enables writeback) |
| `max_iterations` | No | `5` | Maximum factory loop iterations (1-20) |
| `title` | No | — | Human-readable name for the task |
| `acceptance_commands` | No | — | Commands that must pass for the task to succeed |
| `allowed_paths` | No | — | Glob patterns for files the agent can modify |
| `forbidden_paths` | No | — | Glob patterns for files the agent cannot modify |
| `context_files` | No | — | Files to include as context for the LLM |
| `env` | No | — | Environment variables to set when running commands |

### Using the CLI (recommended)

```bash
# Submit a work order (all config is in the file)
aos submit task.md

# Submit and wait for completion
aos submit task.md --wait

# Submit with longer timeout
aos submit task.md --wait --timeout 600

# Check status
aos status <run-id>

# View logs
aos logs <run-id>
```

### Using curl

```bash
# Submit (all config is in the work order file)
curl -X POST http://localhost:8000/runs/submit \
  -F "work_order_md=<task.md"

# Check status
curl http://localhost:8000/runs/<run-id>

# View events
curl http://localhost:8000/runs/<run-id>/events

# List artifacts
curl http://localhost:8000/runs/<run-id>/artifacts

# Download artifact
curl http://localhost:8000/runs/<run-id>/artifacts/summary.json

# Cancel a run
curl -X POST http://localhost:8000/runs/<run-id>/cancel
```

## Monitoring & Debugging

### View Run Status

```bash
aos status <run-id>
```

Output:
```
Run:       7050d927-c0e3-4381-9809-f7b9e642c5a3
Status:    SUCCEEDED
Repo:      https://github.com/user/repo @ main
Iteration: 1
Result:    Decision: PASS, pushed to feature/my-feature
```

### View Event Log

```bash
aos logs <run-id>
```

Output:
```
[2026-01-31 12:00:01] [INFO ] RUN_CREATED          iter=-
[2026-01-31 12:00:05] [INFO ] RUN_START            iter=-
[2026-01-31 12:00:30] [INFO ] SE_OUTPUT            iter=1 writes=2
[2026-01-31 12:00:32] [INFO ] TR_APPLY             iter=1 applied=2 ok=True
[2026-01-31 12:00:35] [INFO ] PO_RESULT            iter=1 decision=PASS
[2026-01-31 12:00:40] [INFO ] RUN_END              iter=-
```

### Database Queries

Connect to the database:
```bash
docker compose exec postgres psql -U aos -d aos
```

Useful queries:
```sql
-- All runs (most recent first)
SELECT id, status, created_at, result_summary 
FROM runs 
ORDER BY created_at DESC 
LIMIT 10;

-- Events for a specific run
SELECT ts, level, kind, iteration, payload 
FROM events 
WHERE run_id = '<run-id>' 
ORDER BY id;

-- Failed runs
SELECT id, status, error, created_at 
FROM runs 
WHERE status = 'FAILED' 
ORDER BY created_at DESC;

-- Artifacts for a run
SELECT name, content_type, bytes, created_at 
FROM artifacts 
WHERE run_id = '<run-id>';

-- Run statistics
SELECT status, COUNT(*) 
FROM runs 
GROUP BY status;
```

### Worker Logs

The RQ worker prints job progress to stdout. Watch for:
- `Job OK` - successful completion
- `exception raised` - job failed (see traceback)

### Artifacts

Artifacts are stored at `/tmp/aos/workspaces/<run-id>/artifacts/`:
- `se_packet_iter_N.json` - SE's proposed changes for iteration N
- `tool_report_iter_N.json` - TR's execution results
- `po_report_iter_N.json` - PO's evaluation
- `summary.json` - Final run summary

List via API:
```bash
curl http://localhost:8000/runs/<run-id>/artifacts
```

Download:
```bash
curl http://localhost:8000/runs/<run-id>/artifacts/summary.json
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/healthz` | Liveness check |
| GET | `/readyz` | Readiness check (DB connected) |
| POST | `/runs` | Create run (JSON body) |
| POST | `/runs/submit` | Create run (form/file upload) |
| GET | `/runs` | List runs (paginated) |
| GET | `/runs/{id}` | Get run status |
| GET | `/runs/{id}/events` | Get run events |
| POST | `/runs/{id}/cancel` | Cancel a run |
| GET | `/runs/{id}/artifacts` | List artifacts |
| GET | `/runs/{id}/artifacts/{name}` | Download artifact |

### List Runs

```bash
# List recent runs
curl http://localhost:8000/runs

# With pagination
curl "http://localhost:8000/runs?limit=50&offset=20"

# Filter by status
curl "http://localhost:8000/runs?status=RUNNING"
curl "http://localhost:8000/runs?status=FAILED&limit=10"
```

Interactive API docs: http://localhost:8000/docs

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | - | OpenAI API key |
| `GITHUB_TOKEN` | For private repos | - | GitHub personal access token |
| `DATABASE_URL` | No | `postgresql://aos:aos_dev@localhost:5432/aos` | Postgres connection |
| `REDIS_URL` | No | `redis://localhost:6379` | Redis connection |
| `WORKSPACE_ROOT` | No | `/tmp/aos/workspaces` | Where to store workspaces |

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Client    │────▶│  FastAPI    │────▶│   Redis     │
│  (aos CLI)  │     │   (API)     │     │   (Queue)   │
└─────────────┘     └──────┬──────┘     └──────┬──────┘
                           │                    │
                           ▼                    ▼
                    ┌─────────────┐     ┌─────────────┐
                    │  Postgres   │◀────│  RQ Worker  │
                    │ (runs, etc) │     │  (factory)  │
                    └─────────────┘     └─────────────┘
                                               │
                                               ▼
                                        ┌─────────────┐
                                        │  LangGraph  │
                                        │  SE→TR→PO   │
                                        └─────────────┘
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev,test]"

# Start services (single command)
source .env && docker compose up -d && alembic upgrade head && honcho start

# Or step by step:
docker compose up -d      # Postgres + Redis
alembic upgrade head      # Migrations
honcho start              # API + Worker (Ctrl+C to stop)

# Run tests
pytest

# Format code
black .
ruff check --fix .

# Type check
mypy src/
```

### What `honcho start` does

Reads the `Procfile` and runs both processes with color-coded output:
- **api** (green): FastAPI server with hot reload
- **worker** (cyan): RQ worker processing jobs

Logs from both are interleaved in one terminal. Press `Ctrl+C` to stop both.

## Troubleshooting

### macOS fork() error
Add this to your `.env` file (already in `.env.example`):
```
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
```

### Run stuck in PENDING
- Check honcho is running (both api and worker should show in output)
- Check Redis is running: `docker compose ps`
- Look for errors in the honcho output

### OpenAI API key error
- Ensure `.env` has `OPENAI_API_KEY=sk-...`
- Source it before starting: `source .env && honcho start`

### Database connection error
- Ensure Postgres is running: `docker compose up -d`
- Run migrations: `alembic upgrade head`

### honcho not found
```bash
pip install -e ".[dev]"
```
