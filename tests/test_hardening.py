import sqlite3

from knowledge_vault_mcp import cli
from knowledge_vault_mcp.backup import backup_state
from knowledge_vault_mcp.evaluation import evaluate, format_report, load_queries
from knowledge_vault_mcp.service import VaultService


def test_backup_state_is_consistent_and_rotated(tmp_path):
    state = tmp_path / "state.db"
    conn = sqlite3.connect(state)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()  # still open, data may live in the WAL only
    dest = tmp_path / "backups"
    for i in range(3):
        old = dest / f"state-2000010{i}-000000.db"
        dest.mkdir(exist_ok=True)
        old.write_bytes(b"")
    target = backup_state(state, dest, keep=2)
    assert sqlite3.connect(target).execute("SELECT x FROM t").fetchone() == (42,)
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert len(list(dest.glob("state-*.db"))) == 2


def test_evaluate_reports_metrics(settings, tmp_path):
    service = VaultService(settings)
    service.vault.open()
    service.sync_and_index()
    path = tmp_path / "q.yaml"
    path.write_text(
        "- query: Sauerteigbrot Mehl\n  relevant: [Kochen/Brot.md]\n  lang: de\n"
        "- query: restic forget\n  relevant: Tools/Restic.md\n  lang: en\n"
        "- query: zzz unknown\n  relevant: [Missing.md]\n"
    )
    report = evaluate(service, load_queries(path), k=3)
    assert report["missing_paths"] == ["Missing.md"]
    keyword = report["modes"]["keyword"]
    assert keyword["de"]["hit@1"] == 1.0 and keyword["en"]["recall@3"] == 1.0
    assert keyword["all"]["n"] == 3 and keyword["not_found"] == ["zzz unknown"]
    assert set(report["modes"]) == {"keyword", "semantic", "hybrid"}
    text = format_report(report, 1.0)
    assert "hybrid" in text and "WARNING" in text


def test_cli_eval_with_separate_model_index(settings, monkeypatch, capsys, tmp_path):
    for key, value in {
        "VAULT_REPO_URL": settings.vault_repo_url,
        "VAULT_PATH": str(settings.vault_path),
        "DB_PATH": str(settings.db_path),
        "EMBEDDING_MODEL": "intfloat/multilingual-e5-small",
        "STATE_DB_PATH": str(settings.state_db_path),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    queries = tmp_path / "q.yaml"
    queries.write_text("- query: Restic\n  relevant: [Tools/Restic.md]\n")
    assert cli.main(["eval", str(queries), "--model", "hash", "--json"]) == 0
    assert '"model": "hash"' in capsys.readouterr().out
    assert (tmp_path / "eval-hash.db").exists()  # the server's index is untouched
    assert not settings.db_path.exists()
