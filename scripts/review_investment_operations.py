#!/usr/bin/env python3
"""Review the local operations snapshot; no external API or queue mutation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ai_stock_assistant.investment_operations import review_queue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=ROOT / "data/reference/investment_operations_queue.json")
    parser.add_argument("--policy", type=Path, default=ROOT / "data/reference/investment_operations_policy.json")
    parser.add_argument("--as-of", default=None, help="Actual UTC review time, e.g. 2026-09-10T09:00:00Z")
    parser.add_argument("--output", type=Path, help="Write the review JSON; omission prints it to stdout")
    args = parser.parse_args()
    if args.output and args.output.resolve() in {args.queue.resolve(), args.policy.resolve()}:
        parser.error("--output must not overwrite the observed queue or policy")
    as_of = args.as_of or datetime.now(timezone.utc).isoformat()
    result = review_queue(json.loads(args.queue.read_text()), json.loads(args.policy.read_text()), as_of)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
        print(json.dumps({"output": str(args.output), "reviewed_as_of": result["reviewed_as_of"], "status_counts": result["status_counts"]}, ensure_ascii=False))
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
