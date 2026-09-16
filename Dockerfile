# One compute-plane image, separate API and worker processes.
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev && useradd --system --uid 10001 --create-home money
COPY migrations ./migrations
COPY alembic.ini UPSTREAM_LOCK.txt REFERENCE_LOCK.txt ./
USER money
EXPOSE 8000
CMD ["uvicorn", "money.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
