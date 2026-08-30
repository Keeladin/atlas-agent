from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

DERIVED_RELATIVE_PATH = ".atlas-derived"

# Deterministic, versioned text extractors. The identity of an extraction is
# (source bytes, extractor config), so these ids are part of that identity and
# must never change meaning in place: a behaviour change is a new version.
EXTRACTORS = ("text@1", "markdown@1", "html@1")
DEFAULT_EXTRACTOR = "text@1"

_SUFFIX_EXTRACTORS = {
    ".txt": "text@1", ".log": "text@1", ".csv": "text@1", ".json": "text@1",
    ".yaml": "text@1", ".yml": "text@1", ".xml": "text@1",
    ".md": "markdown@1", ".markdown": "markdown@1",
    ".html": "html@1", ".htm": "html@1",
}


def extractor_for(relative_path: str) -> str:
    return _SUFFIX_EXTRACTORS.get(Path(relative_path).suffix.casefold(), DEFAULT_EXTRACTOR)


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        lines = [line.strip() for line in joined.splitlines()]
        return "\n".join(line for line in lines if line)


def extract(text: str, extractor: str) -> str:
    """Apply one versioned extractor. Structure only: never interpretation."""
    if extractor not in EXTRACTORS:
        raise ValueError(f"unsupported extractor: {extractor}")
    if extractor in {"text@1", "markdown@1"}:
        return text
    parser = _TextHTMLParser()
    parser.feed(text)
    parser.close()
    return parser.text()
