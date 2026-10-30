#!/usr/bin/env bash
set -euo pipefail

python -m pip install .
python -m playwright install --with-deps chromium

# Fail the release build if the browser binary or its shared libraries cannot
# start. This prevents URL-only Threads ingestion from failing only at runtime.
python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    browser.close()
PY
