FROM astral/uv:python3.12-bookworm-slim as build

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

RUN ["sh", "-c", "apt-get update && apt-get install -y curl build-essential python3-dev libmagic1 libheif-dev git && rm -rf /var/lib/apt/lists/*"]

COPY pyproject.toml uv.lock ./

RUN ["uv", "sync", "--frozen", "--no-cache"]
# RUN uv sync --frozen --no-cache --no-dev

FROM python:3.12-slim-bookworm

WORKDIR /app

RUN ["sh", "-c", "apt-get update && apt-get install -y curl libmagic1 libheif1 git && rm -rf /var/lib/apt/lists/*"]

COPY --from=build /app/.venv /app/.venv

COPY . .

ENV PATH="/app/.venv/bin:$PATH"

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app"]
