FROM ghcr.io/astral-sh/uv:0.11.8 AS uv
FROM python:3.13-slim AS base
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/server/.venv/bin:$PATH
WORKDIR /app/server
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache
COPY app.py gunicorn.conf.py ./
USER 10001:10001

FROM base AS test
COPY test_app.py ./
CMD ["python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-v"]

FROM base AS runtime
ENV CLASH_SETTINGS=/config/server/settings.yaml
EXPOSE 8080
CMD ["gunicorn", "--config", "gunicorn.conf.py", "--bind", "0.0.0.0:8080", "app:create_app()"]
