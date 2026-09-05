from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
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


def main() -> None:
    args = parser().parse_args()
    state_dir = Path(args.state_dir)
    manifest_path = state_dir / "state-manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}. Seed the dashboard-state branch first.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for rel, info in manifest.items():
        if info.get("type") != "split":
            remove(Path(rel))
    copy_tree(state_dir / "data", Path("data"))
    copy_tree(state_dir / "outputs", Path("outputs"))

    for rel, info in manifest.items():
        if info.get("type") != "split":
            continue
        restore_split(state_dir, Path(rel), info)
    print(f"Restored {len(manifest)} files from {state_dir}")


if __name__ == "__main__":
    main()
