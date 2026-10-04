"""Markdown parsing: frontmatter, title, tags and wikilinks.

Code (fenced blocks and inline spans) is masked before tags and links are extracted,
so `#include` in a code block is not a tag and `[[x]]` in backticks is not a link.
Masking keeps character offsets, which lets `rewrite_links` edit the original text.
"""

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import yaml

_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)(?:\r?\n)?^(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S | re.M)
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
_TAG_RE = re.compile(r"(?<![\w/#&\[\]])#([\w/-]+)", re.UNICODE)
_LINK_RE = re.compile(r"(!?)\[\[([^\[\]\n|#^]*)(#\^?[^\[\]\n|]*)?(?:\|([^\[\]\n]*))?\]\]")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


@dataclass
class WikiLink:
    target: str  # as written, e.g. "Folder/Note" or "img.png"
    heading: str | None  # "Section" for [[Note#Section]], without the '#'
    alias: str | None
    embed: bool
    line: int  # 1-based line number in the full file
    context: str  # the line the link appears on, trimmed
    start: int  # offsets of the whole [[...]] in the full file text
    end: int

    @property
    def key(self) -> str:
        return link_key(self.target)


@dataclass
class ParsedNote:
    frontmatter: dict
    body: str
    body_offset: int  # where the body starts in the full text
    title: str
    tags: list[str]
    links: list[WikiLink] = field(default_factory=list)


# ---- frontmatter ----


def split_frontmatter(text: str) -> tuple[dict, str, int]:
    """Return (frontmatter, body, body_offset). Invalid YAML yields an empty dict."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text, 0
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return _jsonable(data), text[m.end() :], m.end()


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def render_frontmatter(data: dict) -> str:
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=None, width=1000)
    return f"---\n{dumped}---\n"


def set_frontmatter_fields(text: str, updates: dict) -> str:
    """Change top-level frontmatter fields in place, keeping the rest of the block as written.

    Only used for server-maintained scalar fields (e.g. `updated`). Lines of other fields
    are left untouched so that formatting and comments in user notes survive edits.
    """
    m = _FM_RE.match(text)
    if not m:
        return text
    block = m.group(1)
    lines = block.split("\n")
    for key, value in updates.items():
        rendered = yaml.safe_dump({key: value}, allow_unicode=True, width=1000).strip()
        pattern = re.compile(rf"^{re.escape(key)}[ \t]*:")
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = rendered
                break
        else:
            lines.append(rendered)
    start = m.start(1)
    return text[:start] + "\n".join(lines) + text[m.end(1) :]


# ---- masking ----


def mask_code(text: str) -> str:
    """Replace code blocks and inline code with spaces, keeping length and newlines."""
    out = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        m = _FENCE_RE.match(line)
        if fence is None and m:
            fence = m.group(1)[0] * len(m.group(1))
            out.append(_blank(line))
        elif fence is not None:
            if m and m.group(1).startswith(fence):
                fence = None
            out.append(_blank(line))
        else:
            out.append(_INLINE_CODE_RE.sub(lambda mm: " " * len(mm.group(0)), line))
    return "".join(out)


def _blank(line: str) -> str:
    return "".join(c if c in "\r\n" else " " for c in line)


# ---- tags, links, title ----


def normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").strip().lower()


def frontmatter_tags(fm: dict) -> list[str]:
    raw = fm.get("tags", fm.get("tag"))
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw))
    return [t for t in (normalize_tag(str(i)) for i in items if i is not None) if t]


def inline_tags(masked_body: str) -> list[str]:
    tags = []
    for m in _TAG_RE.finditer(masked_body):
        tag = m.group(1).rstrip("/-")
        if tag and not tag.replace("/", "").isdigit():
            tags.append(normalize_tag(tag))
    return tags


def link_key(target: str) -> str:
    """Normalized link target used for lookup: lowercase, forward slashes, no `.md`."""
    key = target.strip().replace("\\", "/").lstrip("./").lower()
    return key[:-3] if key.endswith(".md") else key


def path_key(path: str) -> str:
    """The key under which a vault file can be linked by its full path."""
    return link_key(path)


def extract_links(text: str, masked: str | None = None) -> list[WikiLink]:
    masked = mask_code(text) if masked is None else masked
    line_starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
    links = []
    for m in _LINK_RE.finditer(masked):
        target = m.group(2).strip()
        if not target:  # [[#Heading]] points into the same note
            continue
        line_no = _line_of(line_starts, m.start())
        line_end = text.find("\n", m.start())
        context = text[line_starts[line_no - 1] : line_end if line_end != -1 else len(text)].strip()
        heading = m.group(3)[1:].strip() if m.group(3) else None
        links.append(
            WikiLink(
                target=target,
                heading=heading or None,
                alias=m.group(4).strip() if m.group(4) is not None else None,
                embed=m.group(1) == "!",
                line=line_no,
                context=context[:300],
                start=m.start(),
                end=m.end(),
            )
        )
    return links


def _line_of(line_starts: list[int], offset: int) -> int:
    lo, hi = 0, len(line_starts)
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if line_starts[mid] <= offset:
            lo = mid
        else:
            hi = mid
    return lo + 1


def first_heading(masked_body: str, body: str) -> str | None:
    for masked_line, line in zip(masked_body.split("\n"), body.split("\n"), strict=True):
        m = _HEADING_RE.match(masked_line)
        if m and len(m.group(1)) == 1:
            return _HEADING_RE.match(line).group(2).strip()
    return None


def parse_note(path: str, text: str) -> ParsedNote:
    fm, body, offset = split_frontmatter(text)
    masked = mask_code(text)
    masked_body = masked[offset:]
    title = fm.get("title") if isinstance(fm.get("title"), str) and fm["title"].strip() else None
    title = title or first_heading(masked_body, body) or PurePosixPath(path).stem
    tags = list(dict.fromkeys(frontmatter_tags(fm) + inline_tags(masked_body)))
    links = extract_links(text, masked)
    return ParsedNote(
        frontmatter=fm, body=body, body_offset=offset, title=title.strip(), tags=tags, links=links
    )


def rewrite_links(text: str, replace: Callable[[WikiLink], str | None]) -> str:
    """Rewrite the target of each wikilink for which `replace` returns a new target.

    Heading, alias and embed marker are preserved: `![[Old#Sec|Alias]]` -> `![[New#Sec|Alias]]`.
    """
    out, pos = [], 0
    for link in extract_links(text):
        new_target = replace(link)
        if new_target is None:
            continue
        inner = text[link.start : link.end].lstrip("!")[2:-2]
        before_alias, bar, alias = inner.partition("|")
        hash_pos = before_alias.find("#")
        suffix = before_alias[hash_pos:] if hash_pos != -1 else ""
        rendered = ("!" if link.embed else "") + "[[" + new_target + suffix + bar + alias + "]]"
        out.append(text[pos : link.start])
        out.append(rendered)
        pos = link.end
    out.append(text[pos:])
    return "".join(out)
