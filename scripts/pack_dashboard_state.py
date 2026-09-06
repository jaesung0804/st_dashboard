from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import tempfile
import tarfile
from pathlib import Path


DEFAULT_PATHS = [
    "data/dashboard_ews",
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
PART_SIZE = 8 * 1024 * 1024


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


def compressed_parts(source: Path, state_dir: Path, rel: str, part_size: int) -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        compressed = Path(temporary) / "payload.gz"
        with compressed.open("wb") as target, source.open("rb") as original:
            with gzip.GzipFile(filename="", fileobj=target, mode="wb", mtime=0, compresslevel=3) as zipped:
                shutil.copyfileobj(original, zipped, length=1024 * 1024)
        return split_file(compressed, state_dir / ".parts", rel, part_size)


def pack_directory(source: Path, state_dir: Path, rel: str, part_size: int) -> dict:
    # Tens of thousands of retained financial responses must not become tens
    # of thousands of HTTP writes. Tar retains every file byte and relative path.
    files = sorted(path for path in source.rglob("*") if path.is_file())
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "bundle.tar"
        with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as target:
            for path in files:
                if path.is_symlink():
                    raise ValueError(f"State cannot contain symlinks: {path}")
                info = target.gettarinfo(str(path), arcname=path.as_posix())
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                with path.open("rb") as original:
                    target.addfile(info, original)
        with archive.open("rb") as original:
            digest = hashlib.file_digest(original, "sha256").hexdigest()
        return {"type": "archive", "encoding": "gzip", "size": archive.stat().st_size,
                "sha256": digest, "files": len(files),
                "parts": compressed_parts(archive, state_dir, rel, part_size)}


def main() -> None:
    args = parser().parse_args()
    if args.part_size < 1 or args.part_size > PART_SIZE:
        raise ValueError("State parts must be between 1 byte and 8 MiB")
    if args.paths == DEFAULT_PATHS:
        for required in (DEFAULT_PATHS[0], DEFAULT_PATHS[1], DEFAULT_PATHS[2], DEFAULT_PATHS[4], DEFAULT_PATHS[5]):
            if not Path(required).exists():
                raise FileNotFoundError(f"Required live state is missing: {required}")
    state_dir = Path(args.state_dir)
    parts_dir = state_dir / ".parts"
    manifest_path = state_dir / "state-manifest.json"
    for path in [state_dir / "data", state_dir / "outputs", parts_dir, manifest_path]:
        remove(path)
    state_dir.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)
    # These are byte chunks, not standalone text files. Keep this rule in the
    # state repository itself; the app checkout's attributes do not apply here.
    attributes_path = state_dir / ".gitattributes"
    attributes = attributes_path.read_text(encoding="utf-8") if attributes_path.exists() else ""
    for rule in [".parts/** -text", "data/dashboard_ews/** -text", "data/** -text", "outputs/** -text"]:
        if rule not in attributes.splitlines():
            attributes = attributes.rstrip("\n") + "\n" + rule + "\n"
    attributes_path.write_text(attributes, encoding="utf-8")

    manifest: dict[str, dict[str, object]] = {}
    ordinary = []
    for item in args.paths:
        source = Path(item)
        if source.is_dir() and source.as_posix() in ("data/raw/opendart_accounts", "data/raw/yfinance_financials"):
            manifest[source.as_posix()] = pack_directory(source, state_dir, source.as_posix(), args.part_size)
            print(f"Packed {source}: {manifest[source.as_posix()]['files']} retained files", flush=True)
        else:
            ordinary.append(item)
    for source in iter_files(ordinary):
        rel = str(source.resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
        target = state_dir / rel
        size = source.stat().st_size
        with source.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if size > args.part_size:
            # Deterministic gzip reduces transfer/storage without changing a
            # single restored byte. Each API blob remains at most 8 MiB.
            parts = compressed_parts(source, state_dir, rel, args.part_size)
            manifest[rel] = {"type": "split", "encoding": "gzip", "size": size,
                             "parts": parts, "sha256": digest}
            if target.exists():
                target.unlink()
        else:
            copy_small_file(source, target)
            manifest[rel] = {"type": "file", "size": size, "sha256": digest}

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Packed {len(manifest)} files into {state_dir}")


if __name__ == "__main__":
    main()
