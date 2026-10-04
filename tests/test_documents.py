import pytest
from conftest import ToolFailed, tool
from docutil import make_docx, make_pdf

from knowledge_vault_mcp.index.extract import ExtractionError, extract

HTML = """<!doctype html><html><head><title>Runbook  Mail</title><style>p{color:red}</style>
<script>var secret = 1;</script></head><body>
<h1>Mailserver</h1><p>Postfix &amp; Dovecot laufen im Container.</p>
<h2>Backup</h2><ul><li>Maildir nightly</li><li>#hash not a heading</li></ul></body></html>"""


def test_extract_pdf_pages_and_title():
    doc = extract("a/report.pdf", make_pdf(["Quarterly numbers", "Datensicherung (Seite zwei)"], "Q3"))
    assert doc.title == "Q3"
    assert doc.text == "# Page 1\n\nQuarterly numbers\n\n# Page 2\n\nDatensicherung (Seite zwei)"


def test_extract_docx_headings_and_tables():
    doc = extract("x.docx", make_docx("Handbuch", [("Einleitung", "Hallo Welt."), ("Betrieb", "Täglich.")]))
    assert doc.title == "Handbuch"
    assert doc.text == "# Einleitung\n\nHallo Welt.\n\n# Betrieb\n\nTäglich.\n\nKey | Wert"


def test_extract_html():
    doc = extract("page.html", HTML.encode())
    assert doc.title == "Runbook Mail"
    assert "secret" not in doc.text and "color" not in doc.text
    assert doc.text.startswith("# Mailserver\n\nPostfix & Dovecot laufen im Container.\n\n## Backup")
    assert "\\#hash not a heading" in doc.text


def test_extract_errors():
    with pytest.raises(ExtractionError):
        extract("broken.pdf", b"not a pdf")
    with pytest.raises(ExtractionError):
        extract("x.txt", b"text")


@pytest.fixture
def docs_remote(remote):
    remote.commit(
        {
            "Docs/report.pdf": make_pdf(["Quarterly revenue grew", "Datensicherung mit Bandlaufwerk"], "Q3"),
            "Docs/handbuch.docx": make_docx("Handbuch", [("Betrieb", "Der Server startet automatisch.")]),
            "Docs/mail.html": HTML,
            "Docs/broken.pdf": b"%PDF-1.4 garbage",
        },
        "add documents",
    )
    return remote


def test_documents_are_indexed_and_searchable(client, docs_remote):
    stats = client.service.sync_and_index()
    assert stats.indexed == 3 and stats.failed == 1
    hit = tool(client, "search", query="Bandlaufwerk", limit=1)["results"][0]
    assert hit["path"] == "Docs/report.pdf"
    assert hit["kind"] == "pdf" and hit["headings"] == ["Page 2"]
    hit = tool(client, "search", query="startet automatisch", limit=1)["results"][0]
    assert (hit["path"], hit["title"], hit["headings"]) == ("Docs/handbuch.docx", "Handbuch", ["Betrieb"])
    hit = tool(client, "search", query="Dovecot", limit=1)["results"][0]
    assert hit["path"] == "Docs/mail.html" and hit["headings"] == ["Mailserver"]
    note = tool(client, "get_note", path="Docs/report.pdf")
    assert note["kind"] == "pdf" and "Quarterly revenue grew" in note["content"]
    with pytest.raises(ToolFailed):
        tool(client, "get_note", path="Docs/broken.pdf")


def test_documents_can_be_moved_and_links_follow(client, docs_remote):
    docs_remote.commit({"Links.md": "See ![[report.pdf]] and [[Docs/mail.html]]."})
    client.service.sync_and_index()
    tool(client, "move_note", path="Docs/mail.html", new_path="Archive/mail.html")
    assert "[[Archive/mail.html]]" in docs_remote.read("Links.md")
