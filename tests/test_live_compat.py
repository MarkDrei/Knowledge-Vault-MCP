"""Behaviour needed by the live deployment: bind-mounted checkout, local-only commits, no model."""

import pytest
from conftest import ToolFailed, tool

from knowledge_vault_mcp.index.db import IndexDB
from knowledge_vault_mcp.service import VaultService
from knowledge_vault_mcp.writes import NoteWriter, WriteError


@pytest.fixture
def service(settings):
    svc = VaultService(settings)
    svc.vault.open()
    svc.sync_and_index()
    return svc


def test_writes_refused_on_uncommitted_tracked_changes(service):
    (service.vault.root / "Kochen/Brot.md").write_text("edited on the host, not committed")
    with pytest.raises(WriteError, match="uncommitted changes.*Kochen/Brot.md"):
        NoteWriter(service).add_note("New", "x")
    assert service.vault.read_text("Kochen/Brot.md") == "edited on the host, not committed"


def test_untracked_files_survive_a_failed_write(service, monkeypatch):
    (service.vault.root / "draft.md").write_text("untracked draft")

    def boom():
        raise RuntimeError("push failed")

    monkeypatch.setattr(service.vault.repo, "push", boom)
    with pytest.raises(RuntimeError):
        NoteWriter(service).append_note("Kochen/Brot.md", "x")
    assert service.vault.read_text("draft.md") == "untracked draft"
    assert "\nx\n" not in service.vault.read_text("Kochen/Brot.md")


def test_git_sync_off_commits_locally_only(settings, remote):
    settings = settings.model_copy(update={"vault_git_sync": False})
    service = VaultService(settings)
    service.vault.open()
    service.sync_and_index()
    remote.commit({"Remote.md": "# Remote"})
    service.sync_and_index()
    assert not service.vault.exists("Remote.md")  # no pull
    result = NoteWriter(service).add_note("Local only", "x")
    assert service.vault.repo.head() == result.commit
    assert remote.log()[0] == "edit"  # nothing pushed
    assert service.retriever.search("Local only", 1, mode="keyword")[0].path == result.path


def test_uncommitted_edits_are_indexed_and_reverts_rechecked(service):
    note = service.vault.root / "Kochen/Brot.md"
    original = note.read_text()
    (service.vault.root / "Neu.md").write_text("# Neu\n\nquokka")
    note.write_text("# Brot\n\nwombat")
    service.sync_and_index()
    assert [h.path for h in service.retriever.search("quokka", 3, mode="keyword")] == ["Neu.md"]
    assert [h.path for h in service.retriever.search("wombat", 3, mode="keyword")] == ["Kochen/Brot.md"]
    note.write_text(original)  # edit reverted on the host
    (service.vault.root / "Neu.md").unlink()
    service.sync_and_index()
    assert service.retriever.search("wombat", 3, mode="keyword") == []
    assert service.retriever.search("quokka", 3, mode="keyword") == []


def test_embeddings_disabled_is_keyword_only(settings):
    service = VaultService(settings.model_copy(update={"embeddings_enabled": False}))
    service.vault.open()
    service.sync_and_index()
    assert service.status()["semantic_search"] is False
    hits = service.retriever.search("Sauerteigbrot", 3)
    assert hits[0].path == "Kochen/Brot.md" and hits[0].match == "keyword"
    assert service.retriever.search("Sauerteigbrot", 3, mode="semantic") == []


def test_model_load_failure_falls_back_to_keyword_search(settings):
    class Broken:
        name = "broken-model"

        def embed_query(self, text):
            raise OSError("huggingface.co unreachable")

        def embed_passages(self, texts):
            raise OSError("huggingface.co unreachable")

    service = VaultService(settings, embedder=Broken())
    service.vault.open()
    service.load_model()
    stats = service.sync_and_index()
    assert stats.failed == 0 and stats.indexed == 4
    status = service.status()
    assert status["semantic_search"] is False and "unreachable" in status["model_error"]
    assert service.retriever.search("Sauerteigbrot", 1)[0].path == "Kochen/Brot.md"


def test_index_from_another_layout_is_replaced(tmp_path):
    path = tmp_path / "index.db"
    db = IndexDB(path)
    conn = db.conn()
    conn.execute("CREATE TABLE old_chunks (x)")
    conn.execute("CREATE VIRTUAL TABLE old_fts USING fts5(a)")
    conn.execute("DROP TABLE meta")
    db2 = IndexDB(path)
    names = {r[0] for r in db2.conn().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "old_chunks" not in names and "old_fts" not in names and "notes" in names


def test_server_info_reports_live_flags(client):
    info = tool(client, "server_info")
    assert info["inbox_dir"] == "_inbox"
    assert info["index"]["git_sync"] is True and info["index"]["semantic_search"] is True


def test_write_tool_reports_dirty_tree(client):
    (client.service.vault.root / "Tools/Restic.md").write_text("dirty")
    with pytest.raises(ToolFailed, match="uncommitted"):
        tool(client, "append_note", path="Kochen/Brot.md", text="x")
