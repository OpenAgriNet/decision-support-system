# syntax=docker/dockerfile:1
FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /usr/local/bin/

ENV UV_PYTHON_DOWNLOADS=never

# Install dependencies before copying source, for layer caching.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# examples/server.py is a dev harness, not the committed entrypoint (see DSS_ARCHITECTURE.md §8.3).
COPY README.md ./
COPY src/ ./src/
COPY examples/ ./examples/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

RUN python -c "import examples.server"

EXPOSE 8000

CMD ["uvicorn", "examples.server:app", "--host", "0.0.0.0", "--port", "8000"]
