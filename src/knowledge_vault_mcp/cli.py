"""Command line entry point: `kvault serve`, `kvault hash-password`."""

import argparse
import getpass
import sys

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
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
