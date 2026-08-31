from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

DERIVED_RELATIVE_PATH = ".atlas-derived"

# Deterministic, versioned text extractors. The identity of an extraction is
# (source bytes, extractor config), so these ids are part of that identity and
# must never change meaning in place: a behaviour change is a new version.
EXTRACTORS = ("text@1", "markdown@1", "html@1", "pdf@1")
EXTRACTOR_INPUT = {
    "text@1": "text", "markdown@1": "text", "html@1": "text", "pdf@1": "bytes",
}
DEFAULT_EXTRACTOR = "text@1"

_SUFFIX_EXTRACTORS = {
    ".txt": "text@1", ".log": "text@1", ".csv": "text@1", ".json": "text@1",
    ".yaml": "text@1", ".yml": "text@1", ".xml": "text@1",
    ".md": "markdown@1", ".markdown": "markdown@1",
    ".html": "html@1", ".htm": "html@1", ".pdf": "pdf@1",
}


def extractor_for(relative_path: str) -> str:
    return _SUFFIX_EXTRACTORS.get(Path(relative_path).suffix.casefold(), DEFAULT_EXTRACTOR)


class ExtractorUnavailable(RuntimeError):
    pass


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}: self._skip += 1
        elif tag in {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}: self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip: self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip: self.parts.append(data)

    def text(self) -> str:
        lines = [line.strip() for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line)


def _pdf_text(raw: bytes) -> str:
    try:
        import pymupdf
    except ImportError as exc:
        raise ExtractorUnavailable("pdf@1 requires PyMuPDF") from exc
    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"invalid PDF: {exc}") from exc
    parts: list[str] = []
    try:
        for page_number, page in enumerate(doc, 1):
            parts.append(f"# Page {page_number}")
            text = page.get_text("text", sort=True).strip()
            if text: parts.append(text)
            try:
                tables = page.find_tables()
            except Exception:
                tables = None
            if tables is not None:
                for table_number, table in enumerate(getattr(tables, "tables", ()) or (), 1):
                    rows = table.extract() or []
                    if not rows: continue
                    parts.append(f"## Table {page_number}.{table_number}")
                    for row in rows:
                        cells = [str(cell or "").replace("|", "\\|").replace("\n", " ").strip() for cell in row]
                        parts.append("| " + " | ".join(cells) + " |")
    finally:
        doc.close()
    return "\n\n".join(part for part in parts if part).strip()


def extract(value: str | bytes, extractor: str) -> str:
    """Apply one versioned deterministic extractor. Structure only: never interpretation."""
    if extractor not in EXTRACTORS: raise ValueError(f"unsupported extractor: {extractor}")
    expected = EXTRACTOR_INPUT[extractor]
    if expected == "bytes":
        if not isinstance(value, (bytes, bytearray)): raise TypeError(f"{extractor} requires bytes")
        return _pdf_text(bytes(value))
    if not isinstance(value, str): raise TypeError(f"{extractor} requires text")
    if extractor in {"text@1", "markdown@1"}: return value
    parser = _TextHTMLParser(); parser.feed(value); parser.close(); return parser.text()
