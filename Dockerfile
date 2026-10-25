# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.11-slim
RUN useradd --system --uid 10001 --create-home app \
    && mkdir -p /var/lib/rulesmith/uploads \
    && chown -R app:app /var/lib/rulesmith
COPY --from=builder /app/.venv /app/.venv
COPY --chmod=755 deploy/entrypoint.sh /usr/local/bin/rulesmith-entrypoint
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# No `USER app` here: the entrypoint starts as root only to make a root-owned
# volume (Fly mounts them that way) writable, then runs the CMD as `app`
# (uid 10001) via setpriv. See deploy/entrypoint.sh.
WORKDIR /home/app
EXPOSE 8000
ENTRYPOINT ["rulesmith-entrypoint"]
CMD ["uvicorn", "rulesmith.app:app", "--host", "0.0.0.0", "--port", "8000"]
