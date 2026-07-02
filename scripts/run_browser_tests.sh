#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"

cd "${PROJECT_ROOT}"

if ! "${PYTHON_BIN}" -c "import playwright.sync_api; import pytest_playwright.pytest_playwright" >/dev/null 2>&1; then
  echo "Playwright test dependencies are not installed in this venv."
  echo "Run:"
  echo "  ${PYTHON_BIN} -m pip install -r requirements-dev.txt"
  echo "  ${PYTHON_BIN} -m playwright install chromium"
  exit 2
fi

"${PYTHON_BIN}" -m pytest tests/browser -m "browser or e2e" -q
