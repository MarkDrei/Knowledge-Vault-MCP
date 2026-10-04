import pytest
from vaultutil import Remote

from knowledge_vault_mcp.config import Settings
from knowledge_vault_mcp.vault import GitError, Vault


def make_vault(tmp_path, remote_url):
    settings = Settings(_env_file=None, vault_repo_url=remote_url, vault_path=tmp_path / "clone")
    vault = Vault(settings)
    vault.open()
    return vault


def test_clone_and_sync_reports_changes(tmp_path):
    remote = Remote(tmp_path, {"a.md": "# A", "docs/b.md": "# B", ".obsidian/app.json": "{}"})
    vault = make_vault(tmp_path, remote.url)
    assert vault.files() == ["a.md", "docs/b.md"]
    head = vault.repo.head()

    remote.commit({"a.md": "# A2", "c.md": "# C", "docs/b.md": None})
    result = vault.sync()
    assert result.changed and result.old == head
    changes = {(c.status, c.path) for c in vault.changes(result.old, result.new)}
    assert changes == {("M", "a.md"), ("A", "c.md"), ("D", "docs/b.md")}
    assert vault.read_text("a.md") == "# A2"
    times = vault.repo.last_commit_times()
    assert set(times) >= {"a.md", "c.md"}


def test_clone_of_empty_remote(tmp_path):
    remote = Remote(tmp_path)
    vault = make_vault(tmp_path, remote.url)
    assert vault.repo.head() is None
    assert vault.sync().changed is False


def test_local_repository_without_remote(tmp_path):
    vault = make_vault(tmp_path, None)
    assert (tmp_path / "clone" / ".git").is_dir()
    assert vault.sync().changed is False


def test_abspath_rejects_escape(tmp_path):
    vault = make_vault(tmp_path, None)
    (tmp_path / "secret.md").write_text("x")
    (vault.root / "link.md").symlink_to(tmp_path / "secret.md")
    with pytest.raises(PermissionError):
        vault.read_text("link.md")


def test_rewritten_history_requires_full_rescan(tmp_path):
    remote = Remote(tmp_path, {"a.md": "1"})
    vault = make_vault(tmp_path, remote.url)
    assert vault.changes("0" * 40, vault.repo.head()) is None


def test_pull_conflict_raises_and_leaves_clone_clean(tmp_path):
    remote = Remote(tmp_path, {"a.md": "base"})
    vault = make_vault(tmp_path, remote.url)
    (vault.root / "a.md").write_text("local")
    vault.repo.commit(["a.md"], "local edit")
    local_head = vault.repo.head()
    remote.commit({"a.md": "remote"})
    with pytest.raises(GitError):
        vault.sync()
    assert vault.repo.head() == local_head
    assert vault.read_text("a.md") == "local"
