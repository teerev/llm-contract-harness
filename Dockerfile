# ── Stage 1: Build the React frontend ─────────────────────────────────
FROM node:20-slim AS ui-build

WORKDIR /build
COPY web/ui/package.json web/ui/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web/ui/ ./
RUN npm run build

# ── Stage 2: Python runtime ──────────────────────────────────────────
FROM python:3.12-slim

# git is required by the factory (init, commit, push) and by setuptools_scm
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pinned dependencies first (layer cache — doesn't change often)
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Copy application source + pyproject.toml (for the llmch entrypoint)
COPY pyproject.toml ./
COPY planner/ planner/
COPY factory/ factory/
COPY shared/ shared/
COPY llmch/ llmch/
COPY web/server/ web/server/
COPY examples/ examples/

# Install the package itself (non-editable, uses already-installed deps)
# Also install pytest so factory verify/acceptance commands work without
# creating a per-repo venv (avoids runtime pip install + network dependency)
RUN pip install --no-cache-dir --no-deps . && \
    pip install --no-cache-dir pytest

# Copy built frontend from stage 1
COPY --from=ui-build /build/dist/ web/ui/dist/

# Default artifacts directory
RUN mkdir -p /app/artifacts
ENV LLMCH_ARTIFACTS_DIR=/app/artifacts

# Skip per-repo venv creation — pytest is already in the container
ENV LLMCH_SKIP_REPO_VENV=1

# Bind to all interfaces so the container is reachable
ENV LLMCH_HOST=0.0.0.0
EXPOSE 8000

CMD ["python", "-c", "from web.server.main import serve; serve()"]
