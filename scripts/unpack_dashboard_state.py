from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import tempfile
import tarfile
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Restore dashboard state packed for GitHub Actions.")
    p.add_argument("--state-dir", default=".dashboard-state")
    return p


def remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        return
    for path in source.rglob("*"):
        if path.is_file():
            rel = path.relative_to(source)
            output = target / rel
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)


def restore_split(state_dir: Path, output: Path, info: dict) -> None:
    """Validate a split file before replacing an existing local copy."""
    expected = int(info["size"])
    expected_hash = info.get("sha256")
    parts = [state_dir / part for part in info["parts"]]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent, prefix=output.name + ".", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        if info.get("encoding") == "gzip":
            if expected_hash is None:
                raise RuntimeError("Compressed state requires a SHA-256 checksum")
            with tempfile.TemporaryFile() as compressed:
                for part in parts:
                    with part.open("rb") as source:
                        shutil.copyfileobj(source, compressed)
                compressed.seek(0)
                with gzip.GzipFile(fileobj=compressed, mode="rb") as source, temporary.open("wb") as target:
                    copied = 0
                    while chunk := source.read(min(1024 * 1024, expected - copied + 1)):
                        copied += len(chunk)
                        if copied > expected:
                            raise RuntimeError(f"Restored size mismatch for {output}: exceeds {expected}")
                        target.write(chunk)
            verify_file(temporary, info, output)
            temporary.replace(output)
            return
        if info.get("encoding") not in (None, "raw"):
            raise RuntimeError(f"Unknown state encoding: {info['encoding']}")
        boundaries: set[int] = set()
        previous_tail = b""
        with temporary.open("wb") as handle:
            for index, part in enumerate(parts):
                with part.open("rb") as source:
                    if previous_tail == b"\r" and source.read(2) == b"\r\n":
                        boundaries.add(index)
                    source.seek(0)
                    shutil.copyfileobj(source, handle)
                    if source.tell():
                        source.seek(-1, 2)
                        previous_tail = source.read(1)

        actual = temporary.stat().st_size
        if actual != expected:
            # Legacy Windows Git checkouts can turn a CR | LF split boundary
            # into CR | CRLF. Never normalize file contents or waive validation.
            if (
                expected_hash is not None
                or output.suffix.lower() != ".csv"
                or not boundaries
                or actual - expected != len(boundaries)
            ):
                raise RuntimeError(
                    f"Restored size mismatch for {output}: expected {expected}, got {actual}"
                )
            with temporary.open("wb") as handle:
                for index, part in enumerate(parts):
                    with part.open("rb") as source:
                        if index in boundaries:
                            source.seek(1)  # Remove only Git's extra boundary CR.
                        shutil.copyfileobj(source, handle)
            actual = temporary.stat().st_size
            if actual != expected:
                raise RuntimeError(
                    f"Restored size mismatch for {output}: expected {expected}, got {actual}"
                )
            print(f"Repaired {len(boundaries)} legacy CRLF split boundary(s) for {output}")

        if expected_hash is not None:
            with temporary.open("rb") as handle:
                actual_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual_hash != expected_hash:
                raise RuntimeError(f"Restored SHA-256 mismatch for {output}")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def verify_file(path: Path, info: dict, label: Path | None = None) -> None:
    if path.stat().st_size != int(info["size"]):
        raise RuntimeError(f"Restored size mismatch for {label or path}")
    if info.get("sha256"):
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != info["sha256"]:
            raise RuntimeError(f"Restored SHA-256 mismatch for {label or path}")


def safe_relative(rel: str) -> Path:
    # Packed files are data, never arbitrary paths or Git metadata.
    path = Path(rel)
    if (not rel or "\\" in rel or ":" in rel or path.is_absolute()
            or ".." in path.parts or path.parts[0] not in ("data", "outputs", ".parts")):
        raise ValueError(f"Unsafe state path: {rel}")
    return path


def restore_state(state_dir: Path, destination: Path = Path(".")) -> None:
    manifest_path = state_dir / "state-manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}. Seed the dashboard-state branch first.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for rel, info in manifest.items():
        path = safe_relative(rel)
        if path.parts[0] == ".parts" or info.get("type") not in ("file", "split", "archive"):
            raise ValueError(f"Invalid state entry: {rel}")
        if int(info["size"]) < 0:
            raise ValueError(f"Invalid state size: {rel}")
        output = destination / path
        if info["type"] in ("split", "archive"):
            for part in info["parts"]:
                if safe_relative(part).parts[0] != ".parts":
                    raise ValueError(f"Invalid split part: {part}")
            if info["type"] == "split":
                restore_split(state_dir, output, info)
            else:
                with tempfile.TemporaryDirectory() as temporary:
                    archive = Path(temporary) / "bundle.tar"
                    restore_split(state_dir, archive, info)
                    with tarfile.open(archive, "r") as source:
                        members = source.getmembers()
                        names = [item.name for item in members]
                        if len(members) != info["files"] or len(set(names)) != len(names):
                            raise ValueError(f"Archive file count mismatch: {rel}")
                        for member in members:
                            member_path = safe_relative(member.name)
                            if not member.isfile() or path not in member_path.parents:
                                raise ValueError(f"Unsafe archive member: {member.name}")
                        for member in members:
                            target = destination / member.name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with source.extractfile(member) as original, target.open("wb") as handle:
                                shutil.copyfileobj(original, handle)
        else:
            source = state_dir / path
            # Old manifests did not hash small files and Git could change their
            # line endings. New packs validate both size and hash before copying.
            if info.get("sha256"):
                verify_file(source, info)
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
                temporary = Path(handle.name)
            try:
                shutil.copyfile(source, temporary)
                temporary.replace(output)
            finally:
                temporary.unlink(missing_ok=True)
    print(f"Restored {len(manifest)} files from {state_dir}")


def main() -> None:
    args = parser().parse_args()
    restore_state(Path(args.state_dir))


if __name__ == "__main__":
    main()
