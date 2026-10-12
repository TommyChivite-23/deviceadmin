# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEVICEADMIN_DATABASE_PATH=/app/data/deviceadmin.db

WORKDIR /app

# Install dependencies first so this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY deviceadmin ./deviceadmin

# Run as an unprivileged user; /app/data is where the SQLite file lives.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=4).status == 200 else 1)"

# One worker: the page monitor is a background thread and SQLite is a single-writer file.
# Threads give request concurrency; see README "Scaling" for the multi-worker path.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "deviceadmin.wsgi:app"]
