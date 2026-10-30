#!/usr/bin/env bash
set -euo pipefail

python -m pip install .
# Render's native runtime build user cannot elevate to root, so OS packages
# cannot be installed with Playwright's --with-deps option. Install the pinned
# browser binary without privilege escalation and verify the existing runtime
# libraries by launching it below.
PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium

# Fail the release build if the browser binary or its shared libraries cannot
# start. This prevents URL-only Threads ingestion from failing only at runtime.
PLAYWRIGHT_BROWSERS_PATH=0 python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    browser.close()
PY
