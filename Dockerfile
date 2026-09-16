# Separate control-plane and research targets; only research includes native CrewAI.
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12-slim-bookworm AS control-plane

COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" MONEY_ENV=production MONEY_REFERENCE_DATA_DIR=/app/data
ARG MONEY_GIT_SHA=unknown
ENV MONEY_GIT_SHA=$MONEY_GIT_SHA
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY data ./data
RUN uv sync --locked --no-dev --no-editable && useradd --system --uid 10001 --create-home money
COPY migrations ./migrations
COPY alembic.ini UPSTREAM_LOCK.txt REFERENCE_LOCK.txt ./
USER money
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"
CMD ["uvicorn", "money.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]

FROM control-plane AS research
USER root
RUN uv sync --locked --no-dev --no-editable --extra research
USER money
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -m money.worker --healthcheck
CMD ["python", "-m", "money.worker"]

# The default image excludes optional research dependencies and their services.
FROM control-plane AS runtime
