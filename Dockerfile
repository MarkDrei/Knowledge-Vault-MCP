FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 kvault

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

USER kvault
ENV PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0 PORT=8000 \
    STATE_DB_PATH=/data/state.db DB_PATH=/data/index.db VAULT_PATH=/data/vault \
    MODEL_CACHE_DIR=/data/models
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz'); sys.exit(0)"
CMD ["kvault", "serve"]
