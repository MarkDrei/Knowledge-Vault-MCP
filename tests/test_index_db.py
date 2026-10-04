from knowledge_vault_mcp.index import db as index_db
from knowledge_vault_mcp.index.db import IndexDB


def test_schema_created_and_vec_loaded(tmp_path):
    db = IndexDB(tmp_path / "index.db")
    assert db.get_meta("schema_version") == index_db.SCHEMA_VERSION
    assert db.note_count() == 0
    assert db.conn().execute("SELECT vec_version()").fetchone()[0].startswith("v")


def test_schema_version_change_drops_data(tmp_path, monkeypatch):
    path = tmp_path / "index.db"
    db = IndexDB(path)
    db.conn().execute(
        "INSERT INTO notes VALUES ('a.md', 'md', 'A', '{}', NULL, 0, 'h')",
    )
    monkeypatch.setattr(index_db, "SCHEMA_VERSION", "999")
    db2 = IndexDB(path)
    assert db2.note_count() == 0
    assert db2.get_meta("schema_version") == "999"
