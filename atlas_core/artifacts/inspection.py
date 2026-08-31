from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree

MAX_TEXT_PREVIEW_CHARS = 4000
MAX_PACKAGE_PARTS = 2000


def inspect_payload(relative_path: str, raw: bytes, *, complete: bool, observation: dict[str, Any]) -> dict[str, Any]:
    """Return bounded, non-semantic structural observations for one representation."""
    fmt = _format(relative_path, raw)
    result: dict[str, Any] = {
        "inspection_version": "artifact-inspection-v1",
        "format": fmt,
        "inspection_status": "complete" if complete else "partial",
        "compound": False,
        "representations": [],
        "unresolved": [],
        "source_observation": observation,
        "probe": {"bytes_examined": len(raw), "complete": complete},
    }
    if fmt in {"text", "markdown", "html", "xml", "json", "csv"}:
        _inspect_text(result, fmt, raw)
    elif fmt == "pdf":
        _inspect_pdf(result, raw, complete)
    elif fmt in {"docx", "xlsx", "pptx", "zip"}:
        _inspect_package(result, fmt, raw, complete)
    elif fmt in {"png", "jpeg", "gif", "webp"}:
        _inspect_image(result, fmt, raw)
    else:
        result["inspection_status"] = "metadata_only" if not raw else "partial"
        result["unresolved"].append("binary_content")
    return result


def metadata_only_inspection(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "inspection_version": "artifact-inspection-v1",
        "format": "remote_or_unmaterialized",
        "inspection_status": "metadata_only",
        "compound": None,
        "representations": [{"kind": facet["kind"], "status": facet["state"]} for facet in artifact.get("facets", [])],
        "unresolved": ["materialized_content"],
        "probe": {"bytes_examined": 0, "complete": False},
    }


def _format(path: str, raw: bytes) -> str:
    lower = path.casefold()
    if raw.startswith(b"%PDF-"):
        return "pdf"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw.startswith(b"PK\x03\x04"):
        if lower.endswith(".docx"):
            return "docx"
        if lower.endswith(".xlsx"):
            return "xlsx"
        if lower.endswith(".pptx"):
            return "pptx"
        return "zip"
    suffixes = {
        ".md": "markdown", ".markdown": "markdown", ".html": "html", ".htm": "html",
        ".xml": "xml", ".json": "json", ".csv": "csv", ".txt": "text", ".log": "text",
    }
    for suffix, fmt in suffixes.items():
        if lower.endswith(suffix):
            return fmt
    try:
        text = raw.decode("utf-8")
        if "\x00" not in text:
            return "text"
    except UnicodeDecodeError:
        pass
    return "binary"


def _inspect_text(result: dict[str, Any], fmt: str, raw: bytes) -> None:
    text = raw.decode("utf-8", errors="replace")
    preview = text[:MAX_TEXT_PREVIEW_CHARS]
    result["representations"].append({
        "kind": "text", "status": "observed", "preview": preview,
        "preview_truncated": len(text) > len(preview),
    })
    structure: dict[str, Any] = {"lines_in_probe": text.count("\n") + (1 if text else 0)}
    if fmt == "markdown":
        structure["headings_in_probe"] = len(re.findall(r"(?m)^#{1,6}\s+", text))
        images = len(re.findall(r"!\[[^]]*\]\([^)]*\)", text))
        tables = sum(1 for line in text.splitlines() if line.count("|") >= 2)
        if images:
            result["representations"].append({"kind": "image_reference", "status": "observed", "count_in_probe": images})
        if tables:
            result["representations"].append({"kind": "table_like_text", "status": "observed", "rows_in_probe": tables})
    elif fmt == "html":
        images = len(re.findall(r"<img\b", text, flags=re.I))
        tables = len(re.findall(r"<table\b", text, flags=re.I))
        if images:
            result["representations"].append({"kind": "image_reference", "status": "observed", "count_in_probe": images})
        if tables:
            result["representations"].append({"kind": "table", "status": "observed", "count_in_probe": tables})
        result["compound"] = bool(images or tables)
    result["structure"] = structure


def _inspect_pdf(result: dict[str, Any], raw: bytes, complete: bool) -> None:
    pages = len(re.findall(rb"/Type\s*/Page(?:\s|/|>>)", raw))
    images = len(re.findall(rb"/Subtype\s*/Image(?:\s|/|>>)", raw))
    fonts = len(re.findall(rb"/Type\s*/Font(?:\s|/|>>)", raw))
    result["compound"] = True
    result["representations"].append({"kind": "page_document", "status": "observed", "pages_in_probe": pages or None})
    if images:
        result["representations"].append({"kind": "embedded_image", "status": "observed", "count_in_probe": images})
    if fonts:
        result["representations"].append({"kind": "font_resource", "status": "observed", "count_in_probe": fonts})
    result["unresolved"].extend(["document_text", "page_layout", "visual_content"])
    if not complete:
        result["unresolved"].append("remaining_pdf_structure")


def _inspect_package(result: dict[str, Any], fmt: str, raw: bytes, complete: bool) -> None:
    result["compound"] = True
    if not complete:
        result["unresolved"].extend(["package_structure", "package_content"])
        return
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        all_names = zf.namelist()
        names = all_names[:MAX_PACKAGE_PARTS]
    except (zipfile.BadZipFile, OSError):
        result["inspection_status"] = "partial"
        result["unresolved"].append("package_structure")
        return
    result["structure"] = {"package_parts": len(names), "parts_truncated": len(all_names) > len(names)}
    if fmt == "docx":
        _office_docx(result, zf, names)
    elif fmt == "xlsx":
        sheets = [n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml")]
        media = [n for n in names if n.startswith("xl/media/")]
        result["representations"].append({"kind": "spreadsheet", "status": "observed", "worksheet_parts": len(sheets)})
        if media:
            result["representations"].append({"kind": "embedded_image", "status": "observed", "count": len(media)})
        result["unresolved"].extend(["cell_semantics", "charts_and_drawings"])
    elif fmt == "pptx":
        slides = [n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
        media = [n for n in names if n.startswith("ppt/media/")]
        text = _xml_text(zf, slides, 2500)
        result["representations"].append({"kind": "slides", "status": "observed", "count": len(slides), "text_preview": text})
        if media:
            result["representations"].append({"kind": "embedded_image", "status": "observed", "count": len(media)})
        result["unresolved"].append("slide_layout_and_visual_semantics")
    else:
        result["representations"].append({"kind": "package", "status": "observed", "parts": len(names)})
        result["unresolved"].append("package_semantics")


def _office_docx(result: dict[str, Any], zf: zipfile.ZipFile, names: list[str]) -> None:
    media = [n for n in names if n.startswith("word/media/")]
    document = [n for n in names if n == "word/document.xml"]
    preview = _xml_text(zf, document, MAX_TEXT_PREVIEW_CHARS)
    tables = 0
    if document:
        try:
            root = ElementTree.fromstring(zf.read(document[0]))
            tables = sum(1 for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "tbl")
        except (ElementTree.ParseError, KeyError):
            pass
    result["representations"].append({"kind": "text", "status": "observed" if preview else "unknown", "preview": preview})
    if tables:
        result["representations"].append({"kind": "table", "status": "observed", "count": tables})
    if media:
        result["representations"].append({"kind": "embedded_image", "status": "observed", "count": len(media)})
    result["unresolved"].append("document_layout_and_visual_semantics")


def _xml_text(zf: zipfile.ZipFile, names: list[str], limit: int) -> str:
    parts: list[str] = []
    size = 0
    for name in names:
        try:
            root = ElementTree.fromstring(zf.read(name))
        except (ElementTree.ParseError, KeyError):
            continue
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] in {"t", "v"} and node.text:
                parts.append(node.text)
                size += len(node.text) + 1
                if size >= limit:
                    return " ".join(parts)[:limit]
    return " ".join(parts)[:limit]


def _inspect_image(result: dict[str, Any], fmt: str, raw: bytes) -> None:
    rep: dict[str, Any] = {"kind": "image", "status": "observed", "format": fmt}
    if fmt == "png" and len(raw) >= 24:
        rep["width"] = int.from_bytes(raw[16:20], "big")
        rep["height"] = int.from_bytes(raw[20:24], "big")
    result["representations"].append(rep)
    result["unresolved"].append("visual_content")
