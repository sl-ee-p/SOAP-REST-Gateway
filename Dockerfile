# syntax=docker/dockerfile:1

# ---- build stage: install the app and its dependencies into a virtualenv ----------------------
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src

# Copy only what the install needs, so editing tests or docs doesn't invalidate the layer cache.
COPY pyproject.toml README.md ./
COPY app ./app

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install .

# ---- runtime stage: just the virtualenv, no compilers, no pip cache, no source tree -----------
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run as an unprivileged user: a bug in the app must not mean root inside the container.
RUN useradd --system --no-create-home --uid 10001 app
COPY --from=build /opt/venv /opt/venv
USER app

EXPOSE 8000

# python:slim has no curl, so the probe uses the standard library. A non-200 or a connection
# error raises, which makes the command exit non-zero and marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

# One worker on purpose: the cache and rate limiter live in process memory (see DECISIONS.md).
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
