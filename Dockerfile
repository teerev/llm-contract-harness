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
RUN pip install --no-cache-dir --no-deps .

# Copy built frontend from stage 1
COPY --from=ui-build /build/dist/ web/ui/dist/

# Default artifacts directory
RUN mkdir -p /app/artifacts
ENV LLMCH_ARTIFACTS_DIR=/app/artifacts

# Bind to all interfaces so the container is reachable
ENV LLMCH_HOST=0.0.0.0
ENV LLMCH_PORT=8000

EXPOSE 8000

CMD ["python", "-m", "web.server.main"]
