from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--listings-path", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--chunk-size", type=int, default=100)
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--timeout-seconds", type=int, default=300)
    p.add_argument("--retry-failed-small", action="store_true")
    return p


def market_slug(listings_path: Path, end: str) -> str:
    slug = listings_path.stem.removeprefix("us_listings_")
    suffix = f"_{end}"
    return slug[: -len(suffix)] if slug.endswith(suffix) else slug


def run_chunk(args: argparse.Namespace, offset: int, limit: int, batch_size: int) -> bool:
    command = [
        sys.executable,
        "-m",
        "ai_stock_assistant.cli",
        "fetch-us-universe-prices",
        "--listings-path",
        args.listings_path,
        "--start",
        args.start,
        "--end",
        args.end,
        "--offset",
        str(offset),
        "--limit",
        str(limit),
        "--batch-size",
        str(batch_size),
        "--sleep",
        "0",
        "--no-combine",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=args.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        print(f"offset={offset} limit={limit} timeout", flush=True)
        return False
    if completed.returncode != 0:
        print(f"offset={offset} limit={limit} failed rc={completed.returncode}", flush=True)
        if completed.stderr:
            print(completed.stderr[-2000:], flush=True)
        return False
    last_line = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "ok"
    print(f"offset={offset} limit={limit} done {last_line}", flush=True)
    return True


def combine_price_dir(price_dir: Path, output_path: Path) -> tuple[int, int]:
    frames = []
    files = sorted(price_dir.glob("*.csv"))
    for path in files:
        frame = pd.read_csv(path, dtype={"ticker": str})
        if not frame.empty:
            frames.append(frame)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not combined.empty:
        combined = combined.drop_duplicates(["ticker", "date"], keep="last")
        combined = combined.sort_values(["ticker", "date"]).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_path, index=False, encoding="utf-8-sig")
    return len(files), len(combined)


def main() -> None:
    args = parser().parse_args()
    listings_path = Path(args.listings_path)
    listings = pd.read_csv(listings_path, dtype={"ticker": str})
    failed: list[tuple[int, int]] = []
    for offset in range(0, len(listings), args.chunk_size):
        limit = min(args.chunk_size, len(listings) - offset)
        if not run_chunk(args, offset=offset, limit=limit, batch_size=args.batch_size):
            failed.append((offset, limit))

    if args.retry_failed_small and failed:
        retry_failed: list[tuple[int, int]] = []
        for offset, limit in failed:
            for small_offset in range(offset, offset + limit, 10):
                small_limit = min(10, offset + limit - small_offset)
                if not run_chunk(args, offset=small_offset, limit=small_limit, batch_size=1):
                    retry_failed.append((small_offset, small_limit))
        failed = retry_failed

    slug = market_slug(listings_path, args.end)
    price_dir = Path("data/raw/us_ohlcv_daily") / f"{slug}_{args.start}_{args.end}"
    combined_path = Path("data/raw") / f"us_ohlcv_{slug}_{args.start}_{args.end}.csv"
    file_count, row_count = combine_price_dir(price_dir, combined_path)
    print(f"price_dir={price_dir}", flush=True)
    print(f"combined_prices={combined_path}", flush=True)
    print(f"files={file_count} rows={row_count}", flush=True)
    if failed:
        print(f"failed_chunks={failed}", flush=True)


if __name__ == "__main__":
    main()
