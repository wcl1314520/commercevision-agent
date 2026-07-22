FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

ARG UV_INDEX_URL=https://pypi.org/simple

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY alembic.ini ./
COPY config ./config
COPY database ./database
COPY packages ./packages
COPY services ./services

RUN UV_INDEX_URL="${UV_INDEX_URL}" uv sync --frozen --all-packages --no-dev

RUN groupadd --system --gid 10001 commercevision \
    && useradd --system --uid 10001 --gid commercevision \
        --home-dir /nonexistent --shell /usr/sbin/nologin commercevision

ENV PATH="/app/.venv/bin:${PATH}"

USER commercevision

CMD ["uvicorn", "commercevision_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
