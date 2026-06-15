from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


DEFAULT_PATHS = [
    "data/raw/krx_ohlcv_kospi_kosdaq_state.csv",
    "data/raw/krx_listings_kospi_kosdaq_state.csv",
    "data/raw/opendart_financials_state.csv",
    "data/raw/us_ohlcv_nasdaq_nyse_yfinfo_state.csv",
    "data/raw/us_listings_nasdaq_nyse_yfinfo_state.csv",
    "data/raw/yfinance_financials_state.csv",
    "data/raw/krx_kospi_kosdaq_detailed_sector_map.xlsx",
    "data/raw/opendart_corp_codes.csv",
    "data/raw/opendart_accounts",
    "data/raw/yfinance_financials",
]
PART_SIZE = 90 * 1024 * 1024


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pack dashboard state for a GitHub branch.")
    p.add_argument("--state-dir", default=".dashboard-state")
    p.add_argument("--part-size", type=int, default=PART_SIZE)
    p.add_argument("--paths", nargs="*", default=DEFAULT_PATHS)
    return p


def remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def copy_small_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def split_file(source: Path, parts_dir: Path, rel: str, part_size: int) -> list[str]:
    safe_rel = rel.replace("\\", "/")
    target_dir = parts_dir / safe_rel
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    with source.open("rb") as handle:
        index = 0
        while True:
            chunk = handle.read(part_size)
            if not chunk:
                break
            name = f"part-{index:04d}"
            path = target_dir / name
            path.write_bytes(chunk)
            parts.append(str(path.relative_to(parts_dir.parent)).replace("\\", "/"))
            index += 1
    return parts


def iter_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in paths:
        path = Path(item)
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.is_file()))
    return files


def main() -> None:
    args = parser().parse_args()
    state_dir = Path(args.state_dir)
    parts_dir = state_dir / ".parts"
    manifest_path = state_dir / "state-manifest.json"
    for path in [state_dir / "data", state_dir / "outputs", parts_dir, manifest_path]:
        remove(path)
    state_dir.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict[str, object]] = {}
    for source in iter_files(args.paths):
        rel = str(source.resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
        target = state_dir / rel
        size = source.stat().st_size
        if size > args.part_size:
            parts = split_file(source, parts_dir, rel, args.part_size)
            manifest[rel] = {"type": "split", "size": size, "parts": parts}
            if target.exists():
                target.unlink()
        else:
            copy_small_file(source, target)
            manifest[rel] = {"type": "file", "size": size}

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Packed {len(manifest)} files into {state_dir}")


if __name__ == "__main__":
    main()
