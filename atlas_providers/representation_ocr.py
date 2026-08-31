from __future__ import annotations

import json
import os
import sys
from statistics import mean


def _fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _ordered_lines(result) -> list[tuple[str, float]]:
    txts_value = getattr(result, "txts", None)
    scores_value = getattr(result, "scores", None)
    boxes_value = getattr(result, "boxes", None)
    txts = list(txts_value) if txts_value is not None else []
    scores = list(scores_value) if scores_value is not None else []
    boxes = list(boxes_value) if boxes_value is not None else []
    rows = []
    for index, text in enumerate(txts):
        if not str(text).strip():
            continue
        score = float(scores[index]) if index < len(scores) else 0.0
        box = boxes[index] if index < len(boxes) else None
        try:
            y = min(float(point[1]) for point in box)
            x = min(float(point[0]) for point in box)
        except Exception:
            y, x = float(index), 0.0
        rows.append((y, x, str(text).strip(), score))
    rows.sort(key=lambda row: (round(row[0] / 8), row[1], row[0]))
    return [(text, score) for _, _, text, score in rows]
def _ocr_image(engine, image: bytes) -> tuple[list[str], list[float]]:
    result = engine(image)
    rows = _ordered_lines(result)
    return [row[0] for row in rows], [row[1] for row in rows]


def main() -> None:
    if os.environ.get("ATLAS_REPRESENTATION_NEED") != "ocr":
        _fail("rapidocr provider supports only the ocr representation need")
    raw = sys.stdin.buffer.read()
    if not raw:
        _fail("empty representation input")
    try:
        import pymupdf
        from rapidocr import RapidOCR
    except ImportError as exc:
        _fail(f"rapidocr provider dependency unavailable: {exc}")
    engine = RapidOCR()
    source_name = os.environ.get("ATLAS_REPRESENTATION_SOURCE_NAME", "artifact")
    pages: list[str] = []
    scores: list[float] = []
    if raw.startswith(b"%PDF-"):
        try:
            document = pymupdf.open(stream=raw, filetype="pdf")
        except Exception as exc:
            _fail(f"cannot open PDF for OCR: {exc}")
        for index, page in enumerate(document):
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
            lines, page_scores = _ocr_image(engine, pix.tobytes("png"))
            pages.append(f"--- page {index + 1} ---\n" + "\n".join(lines))
            scores.extend(page_scores)
    else:
        lines, image_scores = _ocr_image(engine, raw)
        pages.append("--- image ---\n" + "\n".join(lines))
        scores.extend(image_scores)
    text = "\n\n".join(page.rstrip() for page in pages if page.strip()).strip()
    if not text or not any(line.strip() and not line.startswith("--- ") for line in text.splitlines()):
        _fail("OCR produced no text")
    payload = {
        "text": text,
        "media_type": "text/plain",
        "metadata": {
            "source_name": source_name,
            "pages": len(pages),
            "recognized_lines": len(scores),
            "mean_confidence": round(mean(scores), 6) if scores else None,
            "engine": "rapidocr-onnxruntime",
        },
        "provider_version": "rapidocr@3.9.2-ppocrv6",
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
