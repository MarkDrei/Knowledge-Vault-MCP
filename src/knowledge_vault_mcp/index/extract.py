"""Text extraction for PDF, DOCX and HTML files (ADR-0010).

Each extractor returns a title and Markdown-like text: document headings become `#` lines,
so the normal heading-based chunker gives chunks with a heading path. PDF pages become
`# Page N` sections, which lets search results point at a page. No OCR: scanned PDFs
without a text layer yield no text.
"""

import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath


@dataclass
class Extracted:
    title: str
    text: str


class ExtractionError(ValueError):
    pass


def extract(path: str, data: bytes) -> Extracted:
    suffix = PurePosixPath(path).suffix.lower()
    stem = PurePosixPath(path).stem
    try:
        if suffix == ".pdf":
            return _pdf(data, stem)
        if suffix == ".docx":
            return _docx(data, stem)
        if suffix in (".html", ".htm"):
            return _html(data.decode("utf-8", errors="replace"), stem)
    except ExtractionError:
        raise
    except Exception as e:  # corrupt or encrypted files
        raise ExtractionError(f"cannot extract text from {path}: {e}") from e
    raise ExtractionError(f"unsupported file type: {path}")


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"-\n(?=[a-zäöüß])", "", text)  # re-join words hyphenated across lines
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"^#", r"\\#", text, flags=re.M)  # a '#' at a line start is not a heading
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---- PDF ----


def _pdf(data: bytes, stem: str) -> Extracted:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as e:
            raise ExtractionError("PDF is encrypted") from e
    title = None
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title).strip() or None
    sections = []
    for number, page in enumerate(reader.pages, start=1):
        page_text = _clean(page.extract_text() or "")
        if page_text:
            sections.append(f"# Page {number}\n\n{page_text}")
    return Extracted(title or stem, "\n\n".join(sections))


# ---- DOCX ----


def _docx(data: bytes, stem: str) -> Extracted:
    import docx

    document = docx.Document(io.BytesIO(data))
    lines, first_heading = [], None
    for block in _docx_blocks(document):
        if isinstance(block, tuple):  # (level, heading text)
            level, text = block
            first_heading = first_heading or text
            lines.append(f"{'#' * level} {text}")
        elif block:
            lines.append(_clean(block))
    title = (document.core_properties.title or "").strip() or first_heading or stem
    return Extracted(title, "\n\n".join(line for line in lines if line))


def _docx_blocks(document):
    """Paragraphs in order: headings as (level, text), body as text; tables as rows."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if not text:
                continue
            style = (item.style.name if item.style is not None else "") or ""
            m = re.match(r"(?:Heading|Überschrift)\s*(\d)", style)
            if m:
                yield (min(int(m.group(1)), 6), text)
            elif style == "Title":
                yield (1, text)
            else:
                yield text
        elif isinstance(item, Table):
            for row in item.rows:
                cells = [c.text.strip() for c in row.cells]
                cells = [c for i, c in enumerate(cells) if c and (i == 0 or c != cells[i - 1])]
                if cells:
                    yield " | ".join(cells)


# ---- HTML ----

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "section", "article", "header", "footer", "blockquote", "pre",
    "table", "ul", "ol", "dd", "dt", "figcaption", "main", "aside", "nav",
}  # fmt: skip
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._heading: int | None = None
        self.first_h1: str | None = None
        self._h_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if re.fullmatch(r"h[1-6]", tag):
            self._heading = int(tag[1])
            self._h_buf = []
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if self._heading and tag == f"h{self._heading}":
            text = " ".join("".join(self._h_buf).split())
            if text:
                if self._heading == 1 and self.first_h1 is None:
                    self.first_h1 = text
                self.parts.append(f"\n\n{'#' * self._heading} {text}\n\n")
            self._heading = None
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._skip:
            return
        elif self._heading:
            self._h_buf.append(data)
        else:
            self.parts.append(data)


def _html(source: str, stem: str) -> Extracted:
    parser = _HTMLText()
    parser.feed(source)
    parser.close()
    blocks = []
    for block in re.split(r"\n\s*\n", "".join(parser.parts)):
        block = block.strip()
        if not block:
            continue
        blocks.append(block if re.match(r"#{1,6} ", block) else _clean(block))
    title = " ".join(parser.title.split()) or parser.first_h1 or stem
    return Extracted(title, "\n\n".join(b for b in blocks if b))
