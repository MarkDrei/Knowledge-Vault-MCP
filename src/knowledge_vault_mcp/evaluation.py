"""Retrieval quality evaluation and embedding model benchmark (`kvault eval`).

An evaluation set is a YAML list of queries with the vault paths that should be found:

    - query: Wie sichere ich den VPS?
      relevant: [Ops/Backup-Strategie.md]
      lang: de            # optional, for per-language numbers

For each search mode (keyword, semantic, hybrid) it reports hit@1, recall@k and MRR at
note level (a query counts as found when a chunk of a relevant note is returned), plus
query latency. Passing another model builds a separate index for it, so models can be
compared on the same vault without touching the server's index.
"""

import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from knowledge_vault_mcp.config import Settings
from knowledge_vault_mcp.service import VaultService

MODES = ("keyword", "semantic", "hybrid")


@dataclass
class EvalQuery:
    query: str
    relevant: list[str]
    lang: str | None = None


def load_queries(path: Path) -> list[EvalQuery]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise ValueError("evaluation file must be a YAML list")
    queries = []
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict) or not item.get("query") or not item.get("relevant"):
            raise ValueError(f"entry {i}: needs `query` and a non-empty `relevant` list")
        relevant = item["relevant"] if isinstance(item["relevant"], list) else [item["relevant"]]
        queries.append(EvalQuery(str(item["query"]), [str(r) for r in relevant], item.get("lang")))
    return queries


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


def evaluate(service: VaultService, queries: list[EvalQuery], k: int = 5) -> dict:
    missing = sorted({p for q in queries for p in q.relevant if not service.vault.exists(p)})
    report: dict = {"queries": len(queries), "k": k, "model": service.indexer.model_name, "modes": {}}
    if missing:
        report["missing_paths"] = missing
    for mode in MODES:
        groups: dict[str, list[tuple[float, float, float]]] = {"all": []}
        latencies = []
        misses = []
        for q in queries:
            started = time.perf_counter()
            hits = service.retriever.search(q.query, limit=max(k * 3, 20), mode=mode)
            latencies.append((time.perf_counter() - started) * 1000)
            ranked = list(dict.fromkeys(h.path for h in hits))[:k]  # note level, best chunk first
            relevant = set(q.relevant)
            found = [i for i, p in enumerate(ranked, start=1) if p in relevant]
            row = (
                1.0 if found and found[0] == 1 else 0.0,
                len(relevant & set(ranked)) / len(relevant),
                1.0 / found[0] if found else 0.0,
            )
            groups["all"].append(row)
            if q.lang:
                groups.setdefault(q.lang, []).append(row)
            if not found:
                misses.append(q.query)
        report["modes"][mode] = {
            group: {
                "n": len(rows),
                "hit@1": round(statistics.fmean(r[0] for r in rows), 3),
                f"recall@{k}": round(statistics.fmean(r[1] for r in rows), 3),
                "mrr": round(statistics.fmean(r[2] for r in rows), 3),
            }
            for group, rows in groups.items()
            if rows
        }
        report["modes"][mode]["latency_ms"] = {
            "p50": round(_percentile(latencies, 50), 1),
            "p95": round(_percentile(latencies, 95), 1),
        }
        report["modes"][mode]["not_found"] = misses
    return report


def eval_service(settings: Settings, model: str | None, db_path: Path | None) -> VaultService:
    """A service on the configured vault, optionally with another model and its own index."""
    updates: dict = {}
    if model and model != settings.embedding_model:
        updates["embedding_model"] = model
        safe = model.replace("/", "_")
        updates["db_path"] = db_path or Path(settings.db_path).with_name(f"eval-{safe}.db")
    elif db_path:
        updates["db_path"] = db_path
    return VaultService(settings.model_copy(update=updates) if updates else settings)


def format_report(report: dict, index_seconds: float | None = None) -> str:
    k = report["k"]
    lines = [f"model: {report['model']}   queries: {report['queries']}   k: {k}"]
    if index_seconds is not None:
        lines.append(f"index update: {index_seconds:.1f} s")
    if report.get("missing_paths"):
        lines.append("WARNING: relevant paths not in the vault: " + ", ".join(report["missing_paths"]))
    lines.append(
        f"{'mode':<9} {'group':<6} {'n':>4} {'hit@1':>6} {'recall@' + str(k):>9} {'mrr':>6}  latency p50/p95"
    )
    for mode, data in report["modes"].items():
        lat = data["latency_ms"]
        for group, m in data.items():
            if group in ("latency_ms", "not_found"):
                continue
            recall = m[f"recall@{k}"]
            latency = f"  {lat['p50']:.0f}/{lat['p95']:.0f} ms" if group == "all" else ""
            hit1, mrr = m["hit@1"], m["mrr"]
            lines.append(f"{mode:<9} {group:<6} {m['n']:>4} {hit1:>6.3f} {recall:>9.3f} {mrr:>6.3f}{latency}")
    hybrid_misses = report["modes"].get("hybrid", {}).get("not_found", [])
    if hybrid_misses:
        lines.append(f"hybrid: not found in top {k}: " + "; ".join(hybrid_misses))
    return "\n".join(lines)
