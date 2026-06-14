# Container image for the Navigate REST API (`catalog api` / `navigate api`).
#
# Local-first by design: inside the container the server binds to 0.0.0.0 (so it
# is reachable across the container boundary), but docker-compose only publishes
# the port to the host loopback (127.0.0.1), so the API is still not exposed
# externally by default. Override the published address yourself to change that.
FROM python:3.12-slim AS base

# No bytecode files, unbuffered logs, no pip cache - smaller image, cleaner logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package first (its own layer) so dependency installs are cached
# across source-only changes. The build needs pyproject + README + the source.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# Runtime configuration and query library (read at the working directory).
COPY config ./config
COPY queries ./queries

# Persist the SQLite index and document cache outside the image. These env vars
# point the API (and CLI) at the mounted volume; see catalog.api.config.
ENV NAVIGATE_DB=/data/catalog.sqlite \
    NAVIGATE_CACHE=/data/cache

# Run as a non-root user that owns the data volume.
RUN useradd --create-home --uid 10001 navigate \
    && mkdir -p /data \
    && chown -R navigate:navigate /data /app
USER navigate

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"

# Bind to 0.0.0.0 inside the container; reload is off for a production-style run.
CMD ["catalog", "api", "--no-reload", "--host", "0.0.0.0", "--port", "8000"]
