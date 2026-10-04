import pytest

from knowledge_vault_mcp.vault.links import LinkResolver, candidate_keys, link_target_for
from knowledge_vault_mcp.vault.markdown import (
    mask_code,
    parse_note,
    rewrite_links,
    set_frontmatter_fields,
    split_frontmatter,
)
from knowledge_vault_mcp.vault.paths import PathError, normalize_path

NOTE = """---
title: Backup-Strategie
tags: [ops, Backup]
status: curated
created: 2026-10-01
---
# Ignored heading title

Daily restic runs, see [[Restic#Retention|retention]] and [[Infra/VPS]]. #infra/vps #2026

```bash
# not a heading, #notatag, [[NotALink]]
```

Inline `[[AlsoNot]]` and ![[diagram.png]] plus [[#Local heading]].
"""


def test_parse_note_frontmatter_tags_links():
    note = parse_note("ops/backup.md", NOTE)
    assert note.title == "Backup-Strategie"
    assert note.frontmatter["status"] == "curated"
    assert note.frontmatter["created"] == "2026-10-01"
    assert note.tags == ["ops", "backup", "infra/vps"]
    targets = [(link.target, link.heading, link.alias, link.embed) for link in note.links]
    assert targets == [
        ("Restic", "Retention", "retention", False),
        ("Infra/VPS", None, None, False),
        ("diagram.png", None, None, True),
    ]
    assert note.links[0].line == 9
    assert note.links[0].context.startswith("Daily restic runs")


def test_title_fallbacks():
    assert parse_note("a/x.md", "# Heading One\ntext").title == "Heading One"
    assert parse_note("a/x.md", "no heading").title == "x"


def test_invalid_frontmatter_is_ignored():
    fm, body, _ = split_frontmatter("---\n: [broken\n---\nbody")
    assert fm == {}
    assert body == "body"


def test_mask_code_keeps_length():
    text = "a `b` c\n```\nx\n```\nd"
    masked = mask_code(text)
    assert len(masked) == len(text)
    assert "b" not in masked and "x" not in masked and "d" in masked


def test_set_frontmatter_fields_keeps_other_lines():
    text = "---\ntitle: T  # comment\nupdated: 2020-01-01\n---\nbody"
    out = set_frontmatter_fields(text, {"updated": "2026-10-04T10:00:00+02:00"})
    assert "title: T  # comment" in out
    assert "updated: '2026-10-04T10:00:00+02:00'" in out
    assert out.endswith("---\nbody")


def test_rewrite_links_preserves_heading_alias_embed():
    text = "See [[Old#Sec|Alias]], ![[Old]] and [[Other]]. `[[Old]]`"
    out = rewrite_links(text, lambda link: "New/Place" if link.target == "Old" else None)
    assert out == "See [[New/Place#Sec|Alias]], ![[New/Place]] and [[Other]]. `[[Old]]`"


def test_resolver_obsidian_rules():
    r = LinkResolver(["Note.md", "deep/dir/Note.md", "b/Other.md", "img/pic.png"])
    assert r.resolve("Note") == "Note.md"  # shortest path wins
    assert r.resolve("dir/note") == "deep/dir/Note.md"
    assert r.resolve("Other.md") == "b/Other.md"
    assert r.resolve("pic.png") == "img/pic.png"
    assert r.resolve("missing") is None
    assert candidate_keys("deep/dir/Note.md") == ["deep/dir/note", "dir/note", "note"]
    assert link_target_for("b/Other.md", r, prefer_full_path=False) == "Other"
    assert link_target_for("deep/dir/Note.md", r, prefer_full_path=False) == "deep/dir/Note"


@pytest.mark.parametrize("bad", ["", "/etc/passwd", "../x.md", "a/../../x", ".git/config", "C:/x.md"])
def test_normalize_path_rejects(bad):
    with pytest.raises(PathError):
        normalize_path(bad)


def test_normalize_path_cleans():
    assert normalize_path("./a//b\\c.md") == "a/b/c.md"
