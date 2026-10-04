import pytest
from conftest import tool

from knowledge_vault_mcp.index.chunker import chunk_markdown, split_text
from knowledge_vault_mcp.retrieval.search import SearchFilters, fts_query
from knowledge_vault_mcp.service import VaultService


@pytest.fixture
def service(settings):
    svc = VaultService(settings)
    svc.vault.open()
    svc.sync_and_index()
    return svc


def test_chunk_markdown_heading_paths():
    body = "intro\n# A\ntext a\n## B\ntext b\n```\n# not heading\n```\n# C\n\n"
    chunks = chunk_markdown(body)
    assert [(c.headings, c.text.splitlines()[0]) for c in chunks] == [
        ([], "intro"),
        (["A"], "text a"),
        (["A", "B"], "text b"),
    ]
    assert "# not heading" in chunks[2].text


def test_split_text_respects_limit_and_overlaps():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(20))
    pieces = split_text(text, 500, 80)
    assert len(pieces) > 1
    assert all(len(p) <= 500 for p in pieces)
    assert pieces[1].split()[0] != "Paragraph"  # starts with overlap from the previous piece
    long_word_text = "x" * 1200
    assert all(len(p) <= 500 for p in split_text(long_word_text, 500, 0))


def test_fts_query_is_safe():
    assert fts_query('foo "bar" OR baz*') == '"foo" OR "bar" OR "or" OR "baz"'
    assert fts_query("!!") is None


def test_full_index(service):
    assert service.db.note_count() == 4  # .obsidian ignored
    status = service.status()
    assert status["last_index_run"]["mode"] == "full"
    assert status["indexed_commit"] == service.vault.repo.head()
    tags = {r[0] for r in service.db.conn().execute("SELECT tag FROM tags")}
    assert tags == {"ops", "ops/backup", "infra", "kochen"}


def test_incremental_index(service, remote):
    remote.commit(
        {
            "Tools/Restic.md": "# Restic\n\nNow with rclone.",
            "Neu.md": "# Neu\n\nzebra",
            "Kochen/Brot.md": None,
        }
    )
    stats = service.sync_and_index()
    assert (stats.mode, stats.indexed, stats.removed) == ("incremental", 2, 1)
    assert service.db.note_count() == 4
    assert [h.path for h in service.retriever.search("zebra", 3, mode="keyword")] == ["Neu.md"]
    assert service.retriever.search("Sauerteigbrot", 3, mode="keyword") == []


def test_unchanged_content_is_not_reembedded(service):
    stats = service.indexer.update(force_full=True)
    assert stats.indexed == 0 and stats.unchanged == 4


def test_model_change_triggers_full_rebuild(service):
    service.db.set_meta("embedding_model", "other-model")
    stats = service.indexer.update()
    assert stats.mode == "full" and stats.indexed == 4


def test_hybrid_search_and_filters(service):
    hits = service.retriever.search("Snapshots Aufbewahrung", 5)
    assert hits[0].path == "Ops/Backup-Strategie.md"
    assert hits[0].headings == ["Backup-Strategie", "Aufbewahrung"]
    assert hits[0].match == "both"
    only_tools = service.retriever.search("snapshots", 5, SearchFilters(path_prefix="Tools/"))
    assert {h.path for h in only_tools} == {"Tools/Restic.md"}
    by_tag = service.retriever.search("snapshots", 5, SearchFilters(tags=["ops"]))
    assert {h.path for h in by_tag} == {"Ops/Backup-Strategie.md"}
    by_status = service.retriever.search("snapshots", 5, SearchFilters(status="curated"))
    assert {h.path for h in by_status} == {"Ops/Backup-Strategie.md"}
    future = service.retriever.search("snapshots", 5, SearchFilters(modified_after=4_000_000_000))
    assert future == []


def test_search_tool(client):
    result = tool(client, "search", query="restic retention", limit=3)
    assert result["results"][0]["path"] == "Tools/Restic.md"
    first = result["results"][0]
    assert set(first) >= {"path", "title", "headings", "text", "score", "match", "modified", "tags"}
    assert first["modified"].endswith(("+01:00", "+02:00"))
    filtered = tool(client, "search", query="restic", tags=["kochen"])["results"]
    assert {r["path"] for r in filtered} == {"Kochen/Brot.md"}  # semantic search always returns neighbours


def test_server_info_and_healthz_report_index(client):
    info = tool(client, "server_info")
    assert info["index"]["notes"] == 4 and info["index"]["ready"] is True
    assert client.get("/healthz").json()["index_ready"] is True


def test_cli_reindex_and_search(settings, monkeypatch, capsys):
    from knowledge_vault_mcp import cli

    for key, value in {
        "VAULT_REPO_URL": settings.vault_repo_url,
        "VAULT_PATH": str(settings.vault_path),
        "DB_PATH": str(settings.db_path),
        "EMBEDDING_MODEL": "hash",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(settings.db_path.parent)
    assert cli.main(["reindex"]) == 0
    assert '"mode": "full"' in capsys.readouterr().out
    assert cli.main(["search", "Sauerteigbrot", "--mode", "keyword"]) == 0
    assert "Kochen/Brot.md" in capsys.readouterr().out
