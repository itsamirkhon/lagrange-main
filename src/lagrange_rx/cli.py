"""`lagrange-rx` command line. `serve` binds loopback only unless explicitly overridden (no auth!)."""
from __future__ import annotations

import argparse
import ipaddress
import sys


def is_loopback(host: str) -> bool:
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='lagrange-rx')
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve', help='run the API (+ UI if web/ exists)')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8000)
    serve.add_argument('--data-dir', default=None, help='state directory (default: $LAGRANGE_DATA_DIR or ./data)')
    serve.add_argument('--allow-non-loopback', action='store_true',
                       help='DANGEROUS: the API has no authentication; bind a non-loopback host anyway')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == 'serve':
        if not is_loopback(args.host) and not args.allow_non_loopback:
            print(f'refusing to bind {args.host}: the API has no authentication. '
                  'Use 127.0.0.1, or pass --allow-non-loopback if you really mean it.', file=sys.stderr)
            return 2
        import uvicorn
        from lagrange_rx.api.app import create_app
        uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
