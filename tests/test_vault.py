import subprocess

import pytest

from knowledge_vault_mcp.vault import Vault, VaultError


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "v"
    (root / "10-Projects").mkdir(parents=True)
    (root / ".obsidian").mkdir()
    (root / "10-Projects" / "Ergodox.md").write_text(
        "---\ntitle: Ergodox\ntags: [keyboard, diy]\n---\n# Ergodox\nSplit Tastatur mit Lötarbeit #hardware\n"
    )
    (root / "Index.md").write_text("# Index\nSiehe [[Ergodox]] und [[10-Projects/Ergodox|Board]].\n")
    (root / ".obsidian" / "x.md").write_text("hidden")
    git(root, "init", "-q", "-b", "main")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return Vault(root, inbox_dir="00-Inbox", git_sync=False)


def test_get_and_list(vault):
    n = vault.get_note("10-Projects/Ergodox")
    assert n.title == "Ergodox"
    assert {"keyboard", "diy", "hardware"} <= set(n.tags)
    assert [x["path"] for x in vault.list_notes()] == ["10-Projects/Ergodox.md", "Index.md"]


def test_search_and_filters(vault):
    assert vault.search("tastatur")[0]["path"] == "10-Projects/Ergodox.md"
    assert vault.search("lotarbeit")[0]["path"] == "10-Projects/Ergodox.md"
    assert vault.search("tastatur", tag="diy")
    assert not vault.search("tastatur", tag="nope")
    assert not vault.search("tastatur", path_prefix="Index")


def test_backlinks(vault):
    assert vault.get_backlinks("10-Projects/Ergodox") == [{"path": "Index.md", "title": "Index"}]


def test_path_safety(vault):
    for bad in ("../x", ".obsidian/x", ".git/config"):
        with pytest.raises(VaultError):
            vault.get_note(bad)


def test_add_append_update_commit_and_reindex(vault):
    rel = vault.add_note("VPS Backup Ärger", "Body text zebra", tags=["ops"])
    assert rel.startswith("00-Inbox/") and rel.endswith("-vps-backup-arger.md")
    assert vault.search("zebra")[0]["path"] == rel
    vault.append_note(rel, "more giraffe")
    assert vault.search("giraffe")
    vault.update_note(rel, "# new\nokapi\n")
    assert vault.search("okapi") and not vault.search("zebra")
    log = subprocess.run(
        ["git", "-C", str(vault.root), "log", "--oneline"], capture_output=True, text=True
    ).stdout
    assert log.count("kvault:") == 3


def test_dirty_tree_refused(vault):
    (vault.root / "Index.md").write_text("changed")
    with pytest.raises(VaultError):
        vault.add_note("x", "y")


def test_tools_over_mcp_auth(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


class FakeEmbedder:
    """Concept-based fake: words in the same group map to the same axis (simulates synonyms)."""

    dim = 4
    name = "fake"
    groups = (("auto", "car", "fahrzeug"), ("tastatur", "keyboard"), ("backup", "sicherung"))

    def _vec(self, text):
        low = text.lower()
        return [float(any(w in low for w in g)) + 0.01 for g in self.groups] + [0.01]

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


def test_semantic_hit_without_keyword_overlap(vault, tmp_path):
    v = Vault(
        vault.root, inbox_dir="00-Inbox", git_sync=False, db_path=tmp_path / "i.db", embedder=FakeEmbedder()
    )
    (vault.root / "Plan.md").write_text("# Plan\nNightly backup of the server\n")
    hits = v.search("Sicherung")
    assert hits[0]["path"] == "Plan.md"
    assert hits[0]["matched_by"] == ["semantic"]
    assert v.search("Tastatur")[0]["matched_by"] == ["keyword", "semantic"]


def test_incremental_sync_and_model_change(vault, tmp_path):
    db = tmp_path / "i.db"
    v = Vault(vault.root, inbox_dir="00-Inbox", git_sync=False, db_path=db, embedder=FakeEmbedder())
    assert v.sync_index()["indexed"] == 2
    assert v.sync_index() == {"indexed": 0, "unchanged": 2, "removed": 0}
    (vault.root / "Index.md").write_text("# Index\nnur noch Sicherung\n")
    assert v.sync_index()["indexed"] == 1
    assert v.search("backup")[0]["path"] == "Index.md"
    (vault.root / "Index.md").unlink()
    assert v.sync_index()["removed"] == 1
    # reopening with another model rebuilds from scratch
    other = FakeEmbedder()
    other.name = "other"
    v2 = Vault(vault.root, inbox_dir="00-Inbox", git_sync=False, db_path=db, embedder=other)
    assert v2.sync_index()["indexed"] == 1


def test_status_filter_and_chunks(vault):
    rel = vault.add_note("Chunky", "# A\nalpha text\n## B\nbeta text\n")
    hits = vault.search("beta", status="inbox")
    assert hits[0]["path"] == rel and hits[0]["heading"].endswith("B")
    assert not vault.search("beta", status="curated")
