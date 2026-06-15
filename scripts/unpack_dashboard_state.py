from __future__ import annotations

import argparse
import json
import shutil
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


def main() -> None:
    args = parser().parse_args()
    state_dir = Path(args.state_dir)
    manifest_path = state_dir / "state-manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}. Seed the dashboard-state branch first.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for rel in manifest:
        remove(Path(rel))
    copy_tree(state_dir / "data", Path("data"))
    copy_tree(state_dir / "outputs", Path("outputs"))

    for rel, info in manifest.items():
        if info.get("type") != "split":
            continue
        output = Path(rel)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as handle:
            for part in info["parts"]:
                handle.write((state_dir / part).read_bytes())
        if output.stat().st_size != int(info["size"]):
            raise RuntimeError(f"Restored size mismatch for {rel}")
    print(f"Restored {len(manifest)} files from {state_dir}")


if __name__ == "__main__":
    main()
