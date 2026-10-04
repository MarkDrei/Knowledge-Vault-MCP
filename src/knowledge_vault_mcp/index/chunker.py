"""Splitting notes into chunks for search.

Markdown is split at headings; each chunk keeps its heading path (e.g. `["Backup", "Restic"]`)
as context. Sections longer than `max_chars` are split at paragraph, then sentence, then word
boundaries, and consecutive pieces of one section overlap by about `overlap` characters.
The default of 1500 characters stays well below the 512-token limit of the e5 models.
"""

import re
from dataclasses import dataclass

from knowledge_vault_mcp.vault.markdown import mask_code

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    headings: list[str]
    text: str


def chunk_markdown(body: str, max_chars: int = 1500, overlap: int = 150) -> list[Chunk]:
    sections: list[tuple[list[str], list[str]]] = [([], [])]
    stack: list[tuple[int, str]] = []
    for masked_line, line in zip(mask_code(body).split("\n"), body.split("\n"), strict=True):
        m = _HEADING_RE.match(masked_line)
        if m:
            level = len(m.group(1))
            title = _HEADING_RE.match(line).group(2).strip()
            stack = [(lvl, t) for lvl, t in stack if lvl < level] + [(level, title)]
            sections.append(([t for _, t in stack], []))
        else:
            sections[-1][1].append(line)
    chunks = []
    for headings, lines in sections:
        text = "\n".join(lines).strip()
        if text:
            chunks.extend(Chunk(headings, piece) for piece in split_text(text, max_chars, overlap))
    return chunks


def chunk_plain(text: str, max_chars: int = 1500, overlap: int = 150, headings=None) -> list[Chunk]:
    """Chunks for extracted document text (no Markdown structure)."""
    return [Chunk(list(headings or []), piece) for piece in split_text(text.strip(), max_chars, overlap)]


def split_text(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text] if text else []
    units = _units(text, max_chars)
    pieces: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= max_chars:
            current = candidate
            continue
        pieces.append(current)
        tail = _tail(current, overlap)
        current = f"{tail} {unit}" if tail and len(tail) + len(unit) < max_chars else unit
    if current:
        pieces.append(current)
    return pieces


def _units(text: str, max_chars: int) -> list[str]:
    """Paragraphs, with paragraphs that are too long broken into sentences or words."""
    units = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            units.append(para)
            continue
        for sentence in _SENTENCE_RE.split(para):
            while len(sentence) > max_chars:
                cut = sentence.rfind(" ", 0, max_chars)
                cut = cut if cut > max_chars // 2 else max_chars
                units.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                units.append(sentence)
    return units


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0:
        return ""
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail
