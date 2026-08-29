from __future__ import annotations

import argparse
import ipaddress

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Atlas Companion API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--instance-root", default="instance")
    parser.add_argument("--static-dir", default=None)
    args = parser.parse_args()
    try:
        address = ipaddress.ip_address(args.host)
    except ValueError as exc:
        raise SystemExit("Atlas API host must be an IP address") from exc
    if not address.is_loopback:
        raise SystemExit("Atlas API must bind to loopback; expose it through the authenticated reverse proxy.")
    uvicorn.run(create_app(instance_root=args.instance_root, static_dir=args.static_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
