from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .app import create_app, default_listen
from .auth import DEFAULT_AUTH_ENV_PATH, load_auth_env_file
from .compose import DEFAULT_HOST, DEFAULT_PORT


def main(argv: list[str] | None = None) -> None:
    load_auth_env_file(DEFAULT_AUTH_ENV_PATH)
    host_default, port_default = default_listen()
    parser = argparse.ArgumentParser(description="Atlas Companion API")
    parser.add_argument(
        "--host",
        default=os.environ.get("ATLAS_API_HOST", host_default),
        help=f"Bind host (default {host_default}; production must stay loopback)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ATLAS_API_PORT", str(port_default))),
    )
    parser.add_argument(
        "--work-db",
        default=os.environ.get("ATLAS_WORK_DB", "instance/atlas-work.db"),
    )
    parser.add_argument(
        "--chat-db",
        default=os.environ.get("ATLAS_CHAT_DB", "instance/atlas-chat.db"),
    )
    parser.add_argument(
        "--provider-config",
        default=os.environ.get(
            "ATLAS_PROVIDER_CONFIG", "instance/runtime-providers.json"
        ),
    )
    args = parser.parse_args(argv)

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        # Public TLS terminates at Caddy. The app stays on loopback.
        raise SystemExit(
            f"Refusing to bind {args.host!r}. Use {DEFAULT_HOST}:{DEFAULT_PORT} "
            "behind Caddy (https://atlas-agentic.co.za)."
        )

    app = create_app(
        work_db=Path(args.work_db),
        chat_db=Path(args.chat_db),
        provider_config=Path(args.provider_config),
        host=args.host,
        port=args.port,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
