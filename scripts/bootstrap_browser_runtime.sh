#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv"
LIB_ROOT="${ROOT}/.browser-libs/root"
DEB_ROOT="${ROOT}/.browser-libs/debs"

if [[ ! -x "${VENV}/bin/python" ]]; then
  echo "Atlas virtualenv not found at ${VENV}" >&2
  exit 1
fi

mkdir -p "${LIB_ROOT}" "${DEB_ROOT}"

"${ROOT}/.venv/bin/python" -c 'import playwright' 2>/dev/null || {
  echo "Playwright Python package is not installed. Run: uv sync" >&2
  exit 1
}
packages=(
  libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64
  libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxrender1 libxi6 libxres1
  libgbm1 libasound2t64
)

pushd "${DEB_ROOT}" >/dev/null
apt download "${packages[@]}"
popd >/dev/null

for deb in "${DEB_ROOT}"/*.deb; do
  dpkg-deb -x "${deb}" "${LIB_ROOT}"
done

PLAYWRIGHT_BROWSERS_PATH=0 "${VENV}/bin/playwright" install chromium

LIB_DIR="${LIB_ROOT}/usr/lib/x86_64-linux-gnu"
BROWSER_BIN="$(find "${VENV}/lib" -path '*/playwright/driver/package/.local-browsers/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell' -type f | head -1)"

if [[ -z "${BROWSER_BIN}" ]]; then
  echo "Chromium headless shell was not installed." >&2
  exit 1
fi
missing="$(LD_LIBRARY_PATH="${LIB_DIR}" ldd "${BROWSER_BIN}" | awk '/not found/{print $1}')"
if [[ -n "${missing}" ]]; then
  echo "Browser runtime still has unresolved libraries:" >&2
  echo "${missing}" >&2
  exit 1
fi

ATLAS_BROWSER_LIBRARY_PATH="${LIB_DIR}" "${VENV}/bin/python" - <<'PY'
from atlas_providers.web_browser import PlaywrightBrowserProvider
provider = PlaywrightBrowserProvider()
ok, reason = provider.availability()
if not ok:
    raise SystemExit(f"browser unavailable: {reason}")
page = provider.render("https://example.com", timeout_ms=15000, settle_ms=250, max_chars=5000)
if "Example Domain" not in page.visible_text:
    raise SystemExit("browser smoke test did not return rendered visible text")
print("Atlas browser runtime ready")
PY
