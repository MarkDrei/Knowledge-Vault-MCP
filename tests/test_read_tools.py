import pytest
from conftest import ToolFailed, tool


def test_get_note_by_path(client):
    note = tool(client, "get_note", path="Ops/Backup-Strategie.md")
    assert note["title"] == "Backup-Strategie"
    assert note["frontmatter"]["status"] == "curated"
    assert note["tags"] == ["ops", "ops/backup"]
    assert note["content"].startswith("---\ntags: [ops]")
    assert len(note["sha"]) == 64
    assert note["modified"]
    links = {(link["target"], link["path"], link["heading"]) for link in note["links"]}
    assert links == {("Restic", "Tools/Restic.md", None), ("Infra/VPS", "Infra/VPS.md", "Netz")}


def test_get_note_by_link_name(client):
    assert tool(client, "get_note", path="restic")["path"] == "Tools/Restic.md"
    assert tool(client, "get_note", path="Infra/VPS")["path"] == "Infra/VPS.md"


@pytest.mark.parametrize("bad", ["missing.md", "../etc/passwd", ".git/config"])
def test_get_note_errors(client, bad):
    with pytest.raises(ToolFailed):
        tool(client, "get_note", path=bad)


def test_get_backlinks(client):
    result = tool(client, "get_backlinks", path="Tools/Restic.md")
    assert result["count"] == 2
    by_src = {b["path"]: b for b in result["backlinks"]}
    assert set(by_src) == {"Ops/Backup-Strategie.md", "Infra/VPS.md"}
    assert by_src["Infra/VPS.md"]["alias"] == "restic"
    assert "reverse proxy" in by_src["Infra/VPS.md"]["context"]
    assert tool(client, "get_backlinks", path="Kochen/Brot.md")["count"] == 0


def test_backlinks_follow_sync(client, remote):
    remote.commit({"Neu.md": "Mehr zu [[Brot]]"})
    client.service.sync_and_index()
    result = tool(client, "get_backlinks", path="Kochen/Brot.md")
    assert [b["path"] for b in result["backlinks"]] == ["Neu.md"]
