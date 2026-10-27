FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --locked --no-dev --no-install-project

COPY README.md LICENSE ./
COPY src/ src/

RUN uv sync --locked --no-dev --no-editable


FROM python:3.14-slim

RUN groupadd --system courtier \
    && useradd --system --gid courtier --home-dir /app --shell /usr/sbin/nologin courtier \
    && mkdir /data \
    && chown courtier:courtier /data

ENV PATH="/app/.venv/bin:$PATH" \
    DATABASE_PATH="/data/cache.db" \
    HOT_RELOAD="false"

WORKDIR /app

COPY --from=builder --chown=courtier:courtier /app/.venv /app/.venv

# On Railway, mount the volume at /data (matches DATABASE_PATH) and set RAILWAY_RUN_UID=0
# on the service, because Railway mounts volumes as root.
USER courtier

CMD ["courtier"]
