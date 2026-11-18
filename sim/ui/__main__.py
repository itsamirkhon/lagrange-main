"""CLI entry point for launching the LAGRANGE Rx Simulation Web UI."""

from __future__ import annotations

import argparse
from pathlib import Path

from sim.ui.server import create_server

_DEFAULT_SIM_DIR = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    """Parse CLI flags, start the local Web UI server, and serve until interrupted."""
    parser = argparse.ArgumentParser(description="Run the LAGRANGE Rx Simulation Web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080).")
    parser.add_argument(
        "--sim-dir",
        type=Path,
        default=_DEFAULT_SIM_DIR,
        help="Path to sim/ directory (default: package sim/ directory).",
    )
    args = parser.parse_args(argv)

    server = create_server(args.host, args.port, args.sim_dir)
    actual_port = server.server_address[1]
    print(f"LAGRANGE Rx Simulation UI running at http://{args.host}:{actual_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
