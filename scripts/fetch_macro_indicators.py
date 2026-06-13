from __future__ import annotations

import argparse

from ai_stock_assistant.data.macro import fetch_macro_indicators


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--sleep", type=float, default=0.2)
    return p


def main() -> None:
    args = parser().parse_args()
    result = fetch_macro_indicators(start=args.start, end=args.end, sleep_seconds=args.sleep)
    print(f"long={result.long_path}")
    print(f"wide={result.wide_path}")
    print(f"features={result.feature_path}")
    print(f"manifest={result.manifest_path}")
    print(f"requested={result.requested_count} saved={result.saved_count} failed={result.failed_count}")


if __name__ == "__main__":
    main()
