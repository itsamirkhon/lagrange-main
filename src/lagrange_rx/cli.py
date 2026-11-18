"""`lagrange-rx` command line. `serve` binds loopback only unless explicitly overridden (no auth!)."""
from __future__ import annotations

import argparse
import ipaddress
import os
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
                       help='bind a non-loopback host; requires LAGRANGE_AUTH_PASSWORD (HTTP Basic, user "lagrange")')
    return parser


def load_dotenv(path: str = '.env') -> None:
    """Minimal .env loader (no dependency); real environment variables win over the file."""
    try:
        lines = open(path, encoding='utf-8').read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == 'serve':
        load_dotenv()
        if not is_loopback(args.host):
            if not args.allow_non_loopback:
                print(f'refusing to bind {args.host}: the API has no authentication by default. '
                      'Use 127.0.0.1, or pass --allow-non-loopback together with LAGRANGE_AUTH_PASSWORD.', file=sys.stderr)
                return 2
            if not os.getenv('LAGRANGE_AUTH_PASSWORD'):
                print(f'refusing to bind {args.host}: set LAGRANGE_AUTH_PASSWORD to expose the API beyond loopback.', file=sys.stderr)
                return 2
        import uvicorn
        from lagrange_rx.api.app import create_app
        uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
