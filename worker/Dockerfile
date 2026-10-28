# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install . \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 worker \
    && chown -R worker:worker /app /ms-playwright

USER worker
EXPOSE 10000

CMD ["personal-brain-worker-web"]
