#!/usr/bin/env bash
set -euo pipefail

ROOT="${ATLAS_OCR_PROVIDER_ROOT:-/home/jaco/Projects/atlas-agent-state/representation-ocr}"
VENV="$ROOT/.venv"
mkdir -p "$ROOT"
uv venv --python 3.12 "$VENV"
uv pip install --python "$VENV/bin/python" \
  'rapidocr==3.9.2' 'onnxruntime>=1.22,<2' 'pymupdf>=1.24'
# RapidOCR declares desktop OpenCV; replace it on the headless server.
uv pip uninstall --python "$VENV/bin/python" opencv-python || true
uv pip install --python "$VENV/bin/python" --reinstall 'opencv-python-headless>=4.10,<6'
"$VENV/bin/python" - <<'PY'
import cv2
from rapidocr import RapidOCR
print("RapidOCR provider ready", cv2.__version__)
RapidOCR()
PY
printf '%s\n' "Provider command:"
printf '%s %s\n' "$VENV/bin/python" "/home/jaco/Projects/atlas-agent/atlas_providers/representation_ocr.py"
