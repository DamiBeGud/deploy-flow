FROM ghcr.io/astral-sh/uv:0.10.9 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /srv/backend
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/srv/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1
ENV RUFF_CACHE_DIR=/tmp/deployflow-ruff-cache
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY --chown=10001:10001 . .
RUN useradd --uid 10001 --create-home deployflow
USER deployflow
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
