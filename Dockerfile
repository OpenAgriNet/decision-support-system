# syntax=docker/dockerfile:1

# Build stage: the `-dev` Hardened Image has the shell the install steps need.
# Python is at /usr/bin/python3 in both images, so the venv copies across intact.
FROM dhi.io/python:3.13-dev AS build

WORKDIR /app

# uv comes from its own Hardened Image, not PyPI or ghcr.
COPY --from=dhi.io/uv:0.12 /usr/local/bin/uv /usr/local/bin/uv

ENV UV_PYTHON_DOWNLOADS=never

# Install dependencies before copying source, for layer caching.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY README.md ./
COPY src/ ./src/
# The prompt registry and the per-language templates it names. WORKDIR is
# /app, so the settings default (configs/prompts.yaml) resolves to these.
COPY configs/ ./configs/
COPY prompts/ ./prompts/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# The app writes here at run time: fetched schema packs (var/schema-packs) and
# the evidence files (var/evidence). The runtime image is non-root, so the
# directory is created here and handed to its user below.
RUN mkdir -p /app/var

ENV PATH="/app/.venv/bin:$PATH"

RUN python -c "import dss.entrypoint.app"

# Runtime stage: the plain Hardened Image. No shell, no package manager, and it
# runs as uid 65532, so nothing can be installed here.
FROM dhi.io/python:3.13

WORKDIR /app

COPY --from=build --chown=65532:65532 /app /app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8077

# The base image's entrypoint is empty, so CMD is the whole command.
CMD ["uvicorn", "--factory", "dss.entrypoint.app:create_app", "--host", "0.0.0.0", "--port", "8077"]
