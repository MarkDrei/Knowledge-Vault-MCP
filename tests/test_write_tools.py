import datetime
import re

import pytest
import yaml
from conftest import ToolFailed, tool
from test_oauth_flow import full_login

from knowledge_vault_mcp.vault.markdown import split_frontmatter
from knowledge_vault_mcp.writes import NoteWriter, WriteError, slugify


def frontmatter(text: str) -> dict:
    return split_frontmatter(text)[0]


def test_slugify():
    assert slugify("VPS Backup-Strategie: Größe & Überblick!") == "vps-backup-strategie-groesse-ueberblick"
    assert slugify("!!!") == "note"
    assert len(slugify("word " * 40)) <= 60


def test_add_note_writes_inbox_commits_and_pushes(client, remote):
    result = tool(
        client,
        "add_note",
        title="VPS backup strategy",
        body="Use [[Restic]] nightly. #ops",
        tags=["ops", "#backup"],
        source_url="https://example.com/x",
    )
    path = result["path"]
    assert re.fullmatch(r"_inbox/\d{4}-\d{2}-\d{2}-\d{6}-vps-backup-strategy\.md", path)
    text = remote.read(path)
    fm = frontmatter(text)
    assert fm["id"] == path.removeprefix("_inbox/").removesuffix(".md")
    assert fm["status"] == "inbox" and fm["source"] == "mcp"
    assert fm["captured_by"] == "static-token"
    assert fm["source_url"] == "https://example.com/x"
    assert fm["tags"] == ["ops", "backup"]
    assert fm["created"] == fm["updated"] and fm["created"][-6:] in ("+01:00", "+02:00")
    assert "# VPS backup strategy\n\nUse [[Restic]] nightly." in text
    assert remote.log()[0] == "Add inbox note: VPS backup strategy"
    # indexed immediately, findable, and a backlink of Restic
    hits = tool(client, "search", query="nightly", include_inbox=True)["results"]
    assert hits[0]["path"] == path and hits[0]["status"] == "inbox"
    assert tool(client, "search", query="nightly", include_inbox=False)["results"][0]["path"] != path
    backlinks = tool(client, "get_backlinks", path="Tools/Restic.md")["backlinks"]
    assert path in {b["path"] for b in backlinks}


def test_add_note_name_collision(client, remote):
    writer = NoteWriter(client.service)
    fixed = datetime.datetime(2026, 10, 3, 18, 45, 12, tzinfo=writer.tz)
    writer.now = lambda: fixed
    first = writer.add_note("Same", "one").path
    second = writer.add_note("Same", "two").path
    assert first == "_inbox/2026-10-03-184512-same.md"
    assert second == "_inbox/2026-10-03-184512-same-2.md"
    assert remote.exists(first) and remote.exists(second)


def test_captured_by_uses_oauth_client_name(client):
    _, tokens, _, _ = full_login(client)
    path = tool(client, "add_note", _token=tokens["access_token"], title="From Claude", body="x")["path"]
    assert frontmatter(client.service.vault.read_text(path))["captured_by"] == "Claude"


def test_update_note_with_sha_and_updated_field(client, remote):
    added = tool(client, "add_note", title="Draft", body="v1")
    current = tool(client, "get_note", path=added["path"])
    new_content = current["content"].replace("v1", "v2")
    with pytest.raises(ToolFailed, match="sha mismatch"):
        tool(client, "update_note", path=added["path"], content=new_content, expected_sha="0" * 64)
    result = tool(client, "update_note", path=added["path"], content=new_content, expected_sha=current["sha"])
    text = remote.read(added["path"])
    assert "v2" in text
    assert result["sha"] != current["sha"]
    assert frontmatter(text)["created"] == current["frontmatter"]["created"]
    # notes without frontmatter stay without frontmatter
    tool(client, "update_note", path="Kochen/Brot.md", content="# Brot\n\nNeu.")
    assert remote.read("Kochen/Brot.md") == "# Brot\n\nNeu."


def test_update_without_change_does_not_commit(client, remote):
    content = tool(client, "get_note", path="Tools/Restic.md")["content"]
    before = remote.log()
    result = tool(client, "update_note", path="Tools/Restic.md", content=content)
    assert result["details"]["unchanged"] is True
    assert remote.log() == before


def test_append_note(client, remote):
    tool(client, "append_note", path="Kochen/Brot.md", text="## Variante\n\nMit Roggen.")
    assert remote.read("Kochen/Brot.md").endswith("#kochen\n\n## Variante\n\nMit Roggen.\n")
    hits = tool(client, "search", query="Roggen", limit=1)["results"]
    assert hits[0]["path"] == "Kochen/Brot.md" and hits[0]["headings"][-1] == "Variante"


def test_move_note_rewrites_links(client, remote):
    result = tool(client, "move_note", path="Tools/Restic.md", new_path="Software/Backup/Restic Tool.md")
    assert result["path"] == "Software/Backup/Restic Tool.md"
    assert result["details"]["links_updated"] == {"Ops/Backup-Strategie.md": 1, "Infra/VPS.md": 1}
    assert not remote.exists("Tools/Restic.md")
    assert "Siehe [[Restic Tool]] und [[Infra/VPS#Netz|den VPS]]." in remote.read("Ops/Backup-Strategie.md")
    assert "[[Software/Backup/Restic Tool|restic]]" in remote.read("Infra/VPS.md")
    assert remote.log()[0] == "Move Tools/Restic.md -> Software/Backup/Restic Tool.md"
    backlinks = tool(client, "get_backlinks", path="Software/Backup/Restic Tool.md")
    assert backlinks["count"] == 2


def test_move_keeps_links_that_still_resolve(client, remote):
    result = tool(client, "move_note", path="Tools/Restic.md", new_path="Archive/Restic.md")
    # [[Restic]] still resolves by name; [[Tools/Restic|restic]] used a path and is rewritten
    assert result["details"]["links_updated"] == {"Infra/VPS.md": 1}
    assert "[[Archive/Restic|restic]]" in remote.read("Infra/VPS.md")


def test_move_avoids_stealing_links(client, remote):
    # Moving Kochen/Brot.md to a top-level "Restic.md" would make [[Restic]] resolve to it.
    tool(client, "move_note", path="Kochen/Brot.md", new_path="Restic.md")
    assert "[[Tools/Restic]]" in remote.read("Ops/Backup-Strategie.md")


def test_move_rejects_bad_targets(client):
    with pytest.raises(ToolFailed, match="destination exists"):
        tool(client, "move_note", path="Tools/Restic.md", new_path="Kochen/Brot.md")
    with pytest.raises(ToolFailed, match="extension"):
        tool(client, "move_note", path="Tools/Restic.md", new_path="Tools/Restic.txt")
    with pytest.raises(ToolFailed):
        tool(client, "move_note", path="Tools/Restic.md", new_path="../escape.md")


def test_delete_note(client, remote):
    result = tool(client, "delete_note", path="Tools/Restic.md")
    assert result["details"]["dangling_backlinks"] == ["Infra/VPS.md", "Ops/Backup-Strategie.md"]
    assert not remote.exists("Tools/Restic.md")
    assert all(h["path"] != "Tools/Restic.md" for h in tool(client, "search", query="restic")["results"])


def test_write_errors(client):
    with pytest.raises(ToolFailed, match="not found"):
        tool(client, "append_note", path="nope.md", text="x")
    with pytest.raises(ToolFailed):
        tool(client, "update_note", path=".git/config", content="x")
    with pytest.raises(ToolFailed, match="title is empty"):
        tool(client, "add_note", title="  ", body="x")


def test_conflicting_remote_change_aborts_and_resets(client, remote, monkeypatch):
    """Obsidian pushes between our pull and our push: the push fails, nothing is lost."""
    service = client.service
    writer = NoteWriter(service)
    head_before = service.vault.repo.head()
    real_commit = service.vault.repo.commit

    def commit_then_race(paths, message):
        sha = real_commit(paths, message)
        remote.commit({"Tools/Restic.md": "# Restic\n\nEdited in Obsidian."}, "obsidian edit")
        return sha

    monkeypatch.setattr(service.vault.repo, "commit", commit_then_race)
    with pytest.raises(WriteError, match="aborted"):
        writer.append_note("Tools/Restic.md", "server edit")
    assert service.vault.repo.head() == head_before
    assert "server edit" not in service.vault.read_text("Tools/Restic.md")
    assert remote.read("Tools/Restic.md") == "# Restic\n\nEdited in Obsidian."
    monkeypatch.undo()
    # the next write pulls the Obsidian edit first and succeeds on top of it
    writer.append_note("Tools/Restic.md", "server edit")
    assert remote.read("Tools/Restic.md") == "# Restic\n\nEdited in Obsidian.\n\nserver edit\n"


def test_failed_add_leaves_no_file(client, monkeypatch):
    service = client.service

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(service.vault.repo, "push", boom)
    with pytest.raises(RuntimeError):
        NoteWriter(service).add_note("Lost", "x")
    assert not list((service.vault.root / "_inbox").glob("*lost*"))
    assert not service.vault.repo.has_changes(["_inbox"])


def test_yaml_frontmatter_is_valid(client, remote):
    path = tool(client, "add_note", title='Quote "this": yes', body="x", tags=["a b"])["path"]
    text = remote.read(path)
    block = text.split("---\n")[1]
    assert yaml.safe_load(block)["title"] == 'Quote "this": yes'
