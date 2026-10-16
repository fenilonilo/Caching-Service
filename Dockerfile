# syntax=docker/dockerfile:1

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: this layer stays cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim
RUN useradd --create-home --uid 1000 app && mkdir /data && chown app /data
COPY --from=build /app/.venv /app/.venv
# SQLite under /data by default so the image runs standalone; Compose points it to PostgreSQL.
ENV PATH="/app/.venv/bin:$PATH" \
    CACHE_DATABASE_URL="sqlite+aiosqlite:////data/cache.db"
USER app
EXPOSE 8000
CMD ["uvicorn", "caching_service.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
