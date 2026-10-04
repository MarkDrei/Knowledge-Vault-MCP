"""Hybrid search: BM25 (FTS5) and vector similarity (sqlite-vec), fused with RRF (ADR-0007).

Filters are applied inside both SQL queries, so a restrictive filter never starves the
result list. Vector search is an exact brute-force scan over the filtered chunks, which is
fast enough for the target size (ADR-0002).
"""

import json
import re
from dataclasses import dataclass, field

from knowledge_vault_mcp.index.db import IndexDB
from knowledge_vault_mcp.index.embedder import Embedder, to_blob
from knowledge_vault_mcp.vault.markdown import normalize_tag

RRF_K = 60
MAX_LIMIT = 50
# Column weights for bm25(): title, headings, text.
BM25_WEIGHTS = (5.0, 2.0, 1.0)


@dataclass
class SearchFilters:
    tags: list[str] = field(default_factory=list)  # all must match; `infra` also matches `infra/vps`
    path_prefix: str | None = None
    status: str | None = None
    include_inbox: bool = True
    modified_after: int | None = None  # unix time, inclusive
    modified_before: int | None = None  # unix time, exclusive


@dataclass
class Hit:
    path: str
    title: str
    headings: list[str]
    text: str
    score: float
    match: str  # keyword | semantic | both
    kind: str
    status: str | None
    tags: list[str]
    modified: int | None


def fts_query(query: str) -> str | None:
    """Turn free text into a safe FTS5 query: every word as a quoted term, OR-combined."""
    terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 1 or t.isdigit()]
    terms = list(dict.fromkeys(terms))
    return " OR ".join(f'"{t}"' for t in terms) if terms else None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Retriever:
    def __init__(self, db: IndexDB, embedder: Embedder | None, inbox_dir: str):
        self.db = db
        self.embedder = embedder
        self.inbox_dir = inbox_dir.strip("/")

    def _where(self, f: SearchFilters) -> tuple[str, list]:
        clauses, params = [], []
        if f.path_prefix:
            clauses.append("c.path LIKE ? ESCAPE '\\'")
            params.append(_escape_like(f.path_prefix.strip().lstrip("/")) + "%")
        for tag in f.tags:
            tag = normalize_tag(tag)
            clauses.append(
                "EXISTS (SELECT 1 FROM tags t WHERE t.path = c.path "
                "AND (t.tag = ? OR t.tag LIKE ? ESCAPE '\\'))"
            )
            params += [tag, _escape_like(tag) + "/%"]
        if f.status:
            clauses.append("n.status = ?")
            params.append(f.status)
        if not f.include_inbox:
            clauses.append("NOT (c.path LIKE ? ESCAPE '\\' OR coalesce(n.status, '') = 'inbox')")
            params.append(_escape_like(self.inbox_dir) + "/%")
        if f.modified_after is not None:
            clauses.append("n.modified >= ?")
            params.append(f.modified_after)
        if f.modified_before is not None:
            clauses.append("n.modified < ?")
            params.append(f.modified_before)
        return (" AND " + " AND ".join(clauses)) if clauses else "", params

    def keyword_ranking(self, query: str, filters: SearchFilters, k: int) -> list[int]:
        match = fts_query(query)
        if not match:
            return []
        where, params = self._where(filters)
        sql = (
            "SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            "JOIN notes n ON n.path = c.path "
            f"WHERE chunks_fts MATCH ?{where} ORDER BY bm25(chunks_fts, ?, ?, ?) LIMIT ?"
        )
        rows = self.db.conn().execute(sql, [match, *params, *BM25_WEIGHTS, k])
        return [r[0] for r in rows]

    def vector_ranking(self, query: str, filters: SearchFilters, k: int) -> list[int]:
        vector = to_blob(self.embedder.embed_query(query))
        where, params = self._where(filters)
        sql = (
            "SELECT c.id FROM chunks c JOIN notes n ON n.path = c.path "
            f"WHERE c.embedding IS NOT NULL{where} "
            "ORDER BY vec_distance_cosine(c.embedding, ?) LIMIT ?"
        )
        rows = self.db.conn().execute(sql, [*params, vector, k])
        return [r[0] for r in rows]

    def search(
        self, query: str, limit: int = 8, filters: SearchFilters | None = None, mode: str = "hybrid"
    ) -> list[Hit]:
        filters = filters or SearchFilters()
        limit = max(1, min(limit, MAX_LIMIT))
        k = max(50, limit * 5)
        rankings: dict[str, list[int]] = {}
        if mode in ("hybrid", "keyword"):
            rankings["keyword"] = self.keyword_ranking(query, filters, k)
        if mode in ("hybrid", "semantic") and self.embedder is not None:
            rankings["semantic"] = self.vector_ranking(query, filters, k)
        scores: dict[int, float] = {}
        sources: dict[int, set[str]] = {}
        for name, ids in rankings.items():
            for rank, chunk_id in enumerate(ids, start=1):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
                sources.setdefault(chunk_id, set()).add(name)
        top = sorted(scores, key=lambda i: scores[i], reverse=True)[:limit]
        return [self._hit(i, scores[i], sources[i]) for i in top]

    def _hit(self, chunk_id: int, score: float, sources: set[str]) -> Hit:
        conn = self.db.conn()
        path, headings, text, title, kind, status, modified = conn.execute(
            "SELECT c.path, c.headings, c.text, n.title, n.kind, n.status, n.modified "
            "FROM chunks c JOIN notes n ON n.path = c.path WHERE c.id = ?",
            (chunk_id,),
        ).fetchone()
        tags = [r[0] for r in conn.execute("SELECT tag FROM tags WHERE path = ? ORDER BY tag", (path,))]
        return Hit(
            path=path,
            title=title,
            headings=json.loads(headings),
            text=text,
            score=round(score, 6),
            match="both" if len(sources) > 1 else next(iter(sources)),
            kind=kind,
            status=status,
            tags=tags,
            modified=modified,
        )
