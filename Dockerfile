FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY . .
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/logs /app/output \
    && chown -R appuser:appuser /app

USER appuser

CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 2 --timeout 900 --bind 0.0.0.0:${PORT:-10000} web_app:app"]
