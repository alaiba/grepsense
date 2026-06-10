"""grepsense command-line entry point.

    grepsense serve [--transport stdio|http] [--host H] [--port P]
    grepsense embed [--root PATH] [--repo NAME] [--reset] [--full]
    grepsense status [--root PATH]
    grepsense version
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grepsense",
        description="Two-modal code search (lexical + semantic) for AI agents, over MCP.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="Run the MCP server")
    s.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("GREPSENSE_TRANSPORT", "stdio"),
    )
    s.add_argument("--host", default=os.environ.get("GREPSENSE_HTTP_HOST", "127.0.0.1"))
    s.add_argument(
        "--port", type=int, default=int(os.environ.get("GREPSENSE_HTTP_PORT", "8765"))
    )

    e = sub.add_parser("embed", help="Build/refresh semantic embeddings")
    e.add_argument("--root", help="Root to index (overrides GREPSENSE_ROOT)")
    e.add_argument("--repo", help="Limit to a single repo by name")
    e.add_argument("--reset", action="store_true", help="Wipe collections and re-baseline")
    e.add_argument(
        "--full",
        action="store_true",
        help="Full embed all repos (ignore incremental state)",
    )

    st = sub.add_parser("status", help="Show per-repo embed state")
    st.add_argument("--root", help="Root to inspect (overrides GREPSENSE_ROOT)")

    sub.add_parser("version", help="Print the grepsense version")

    args = parser.parse_args(argv)

    if args.command == "version":
        print(f"grepsense {__version__}")
        return 0

    if args.command == "serve":
        from . import server

        server.run(transport=args.transport, host=args.host, port=args.port)
        return 0

    if args.command == "embed":
        if args.root:
            os.environ["GREPSENSE_ROOT"] = args.root
        from .config import Config
        from . import incremental

        cfg = Config.load()
        result = incremental.run_once(
            cfg,
            repo_filter=args.repo,
            reset=args.reset,
            incremental=not args.full,
        )
        print(
            f"grepsense: embedded {result['chunks']} chunks from {result['files']} "
            f"files across {len(result['repos'])} repo(s); collection "
            f"'{cfg.collection}' now has {result['collection_count']} vectors"
        )
        return 0

    if args.command == "status":
        if args.root:
            os.environ["GREPSENSE_ROOT"] = args.root
        from .config import Config
        from . import incremental

        cfg = Config.load()
        print(incremental.format_status(cfg))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
