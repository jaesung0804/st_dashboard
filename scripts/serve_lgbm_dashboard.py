from __future__ import annotations

import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the LGBM warning dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--directory", type=Path, default=Path("outputs/lgbm_warning_dashboard"))
    args = parser.parse_args()

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(args.directory))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"http://{args.host}:{args.port}/dashboard.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
