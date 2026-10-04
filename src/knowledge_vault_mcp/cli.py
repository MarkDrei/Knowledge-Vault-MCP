"""Command line entry point: `kvault serve | hash-password | reindex | search | eval | backup`."""

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path

from knowledge_vault_mcp import __version__


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from knowledge_vault_mcp.config import Settings
    from knowledge_vault_mcp.server import create_app

    settings = Settings()
    if settings.owner_password_hash is None and settings.auth_token is None:
        print(
            "Refusing to start: set OWNER_PASSWORD_HASH (see `kvault hash-password`) or AUTH_TOKEN.",
            file=sys.stderr,
        )
        return 2
    uvicorn.run(
        create_app(settings),
        host=args.host or settings.host,
        port=args.port or settings.port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )
    return 0


def _hash_password(args: argparse.Namespace) -> int:
    from knowledge_vault_mcp.auth.passwords import hash_password

    pw = getpass.getpass("Owner password: ")
    if len(pw) < 12:
        print("Use at least 12 characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat: ") != pw:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    print(hash_password(pw))
    return 0


def _reindex(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from knowledge_vault_mcp.config import Settings
    from knowledge_vault_mcp.service import VaultService

    service = VaultService(Settings())
    service.vault.open()
    if not args.no_pull:
        service.vault.sync()
    stats = service.indexer.update(force_full=args.full)
    print(json.dumps(asdict(stats)))
    return 1 if stats.failed else 0


def _search(args: argparse.Namespace) -> int:
    from knowledge_vault_mcp.config import Settings
    from knowledge_vault_mcp.retrieval.search import SearchFilters
    from knowledge_vault_mcp.service import VaultService

    service = VaultService(Settings())
    filters = SearchFilters(tags=args.tag or [], path_prefix=args.path)
    for hit in service.retriever.search(args.query, args.limit, filters, mode=args.mode):
        heading = " > ".join(hit.headings)
        print(f"{hit.score:.4f}  [{hit.match}]  {hit.path}  {heading}")
        print("        " + hit.text[:160].replace("\n", " "))
    return 0


def _eval(args: argparse.Namespace) -> int:
    from knowledge_vault_mcp.config import Settings
    from knowledge_vault_mcp.evaluation import eval_service, evaluate, format_report, load_queries

    queries = load_queries(args.file)
    service = eval_service(Settings(), args.model, args.db)
    service.vault.open()
    if not args.no_pull:
        service.vault.sync()
    stats = service.indexer.update()
    report = evaluate(service, queries, k=args.k)
    report["index_seconds"] = stats.seconds
    print(
        json.dumps(report, indent=2, ensure_ascii=False)
        if args.json
        else format_report(report, stats.seconds)
    )
    return 0


def _backup(args: argparse.Namespace) -> int:
    from knowledge_vault_mcp.backup import backup_state
    from knowledge_vault_mcp.config import Settings

    print(backup_state(Settings().state_db_path, args.dest, keep=args.keep))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kvault", description="Knowledge-Vault-MCP server")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the HTTP MCP server")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.set_defaults(func=_serve)
    hp = sub.add_parser("hash-password", help="print a hash for OWNER_PASSWORD_HASH")
    hp.set_defaults(func=_hash_password)
    ri = sub.add_parser("reindex", help="pull and bring the search index up to date")
    ri.add_argument("--full", action="store_true", help="re-scan every file (also after deleting index.db)")
    ri.add_argument("--no-pull", action="store_true", help="index the local clone without pulling")
    ri.set_defaults(func=_reindex)
    se = sub.add_parser("search", help="query the index from the command line")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=8)
    se.add_argument("--tag", action="append")
    se.add_argument("--path")
    se.add_argument("--mode", choices=["hybrid", "keyword", "semantic"], default="hybrid")
    se.set_defaults(func=_search)
    ev = sub.add_parser("eval", help="measure retrieval quality / benchmark an embedding model")
    ev.add_argument("file", type=Path, help="YAML evaluation set (see eval/queries.example.yaml)")
    ev.add_argument("--model", help="embedding model to benchmark (builds a separate index)")
    ev.add_argument("--db", type=Path, help="index file to use for --model")
    ev.add_argument("--k", type=int, default=5)
    ev.add_argument("--json", action="store_true")
    ev.add_argument("--no-pull", action="store_true")
    ev.set_defaults(func=_eval)
    bk = sub.add_parser("backup", help="consistent copy of state.db (OAuth clients and tokens)")
    bk.add_argument("dest", type=Path, help="backup directory")
    bk.add_argument("--keep", type=int, default=14, help="number of backups to keep (0 = all)")
    bk.set_defaults(func=_backup)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
