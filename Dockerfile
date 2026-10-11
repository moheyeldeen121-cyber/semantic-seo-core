# Semantic SEO Tool — production image (Render / any Docker host)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CACHE_DIR=/tmp/seo-cache \
    PORT=10000

WORKDIR /app

# Dependencies first (cached layer). Chromium + its system libraries are
# installed by Playwright itself, so the browser always matches the package.
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/* \
 && chmod -R a+rX /ms-playwright

# Run as an unprivileged user: the headless browser opens untrusted pages.
RUN useradd --create-home --uid 10001 appuser
COPY . .
USER appuser

EXPOSE 10000

# ONE worker (jobs, cancel tokens and rate limits live in this process),
# several threads for concurrent requests; long timeout for streamed analyses.
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-10000} --worker-class gthread --workers 1 --threads 8 --timeout 600 --graceful-timeout 30 --keep-alive 75 --access-logfile -"]
