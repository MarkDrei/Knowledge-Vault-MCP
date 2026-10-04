# ADR-0010: Document text extraction with pypdf, python-docx and html.parser

Status: accepted (2026-10-04)

**Context.** PDF, DOCX and HTML files in the vault should be searchable (README, "Vault content"). Options range from heavy all-in-one toolkits (unstructured, Apache Tika, docling: large dependencies, Java or ML models) to small single-format libraries. OCR and layout analysis are non-goals for v1.

**Decision.**
- PDF: `pypdf` (pure Python). One section per page, headed `# Page N`, so hits name the page. The PDF metadata title is used as the note title if present.
- DOCX: `python-docx`. Heading styles (`Heading n` / `Überschrift n`, `Title`) become Markdown headings; tables become `cell | cell` lines; the core-properties title or first heading is the title.
- HTML: the standard library `html.parser`. `h1`–`h6` become headings, `script`/`style`/`head` are dropped, `<title>` is the title.
- The extracted text runs through the normal heading-based chunker. A `#` at the start of extracted body text is escaped so it is not mistaken for a heading.
- Files larger than `MAX_DOCUMENT_MB` (default 25) are skipped; a file that fails to parse is logged and counted as failed without stopping the index run.

**Consequences.** Three small pure-Python dependencies, no system packages. Text quality depends on the PDF's text layer: scanned PDFs yield nothing, and multi-column layouts may interleave. If that becomes a problem, swap the PDF extractor (e.g. `pdfminer.six` or `pymupdf`) behind `index/extract.py`.
