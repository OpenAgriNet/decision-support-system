# syntax=docker/dockerfile:1

# --- Optional: compress a name model to ONNX (ADR-0016) --------------------
# Used only by the dss-indicner target; a plain build skips it. torch lives
# here and never reaches an image the DSS runs in. IndicNER is gated, so the
# build needs a Hugging Face token as a secret (never stored in a layer):
#   docker build --target dss-indicner --secret id=hf_token,env=HF_TOKEN .
FROM python:3.13-slim AS onnx-ner-export
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/
WORKDIR /export
COPY scripts/export_onnx_ner.py ./
ARG NER_MODEL=ai4bharat/IndicNER
RUN --mount=type=secret,id=hf_token,env=HF_TOKEN \
    --mount=type=cache,target=/root/.cache \
    uv run --no-project --python 3.13 \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        --with torch --with transformers --with onnx --with onnxruntime \
        python export_onnx_ner.py --model "$NER_MODEL" --out /model

# --- The DSS ----------------------------------------------------------------
FROM python:3.13-slim AS dss

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /usr/local/bin/

ENV UV_PYTHON_DOWNLOADS=never

# Install dependencies before copying source, for layer caching.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY README.md ./
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

RUN python -c "import dss.entrypoint.app"

EXPOSE 8077

CMD ["uvicorn", "--factory", "dss.entrypoint.app:create_app", "--host", "0.0.0.0", "--port", "8077"]

# --- The DSS with the IndicNER name model baked in ----------------------------
# Add `- {type: onnx, dir: /app/var/models/indicner}` to the redaction rules'
# `identifiers:` to use it.
FROM dss AS dss-indicner
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra ner-indic
COPY --from=onnx-ner-export /model /app/var/models/indicner

# --- Default target: a plain `docker build` gives the DSS without the model ---
FROM dss
