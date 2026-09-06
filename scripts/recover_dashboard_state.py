"""Restore a pinned training backup without calling collection or training."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import zipfile

from ai_stock_assistant.monthly_ews import PRICE_FILES, digest, load_month, read_json, verify_prediction
from unpack_dashboard_state import restore_state


def extract_backup(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if sum(entry.file_size for entry in entries) > 3 * 1024 ** 3:
            raise ValueError("Recovery archive exceeds the 3 GiB unpacked bound")
        seen = set()
        for entry in entries:
            name = entry.filename.replace("\\", "/").rstrip("/")
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or ".." in path.parts or ":" in name
                    or path.parts[0] not in ("data", "outputs", ".parts", ".gitattributes", "state-manifest.json", "README.md")
                    or ".git" in path.parts or stat.S_ISLNK(entry.external_attr >> 16)
                    or name in seen):
                raise ValueError(f"Unsafe recovery archive entry: {name}")
            seen.add(name)
        for entry in entries:
            target = destination / entry.filename.replace("\\", "/")
            if entry.is_dir() or entry.filename.endswith("\\"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as original, target.open("wb") as output:
                    shutil.copyfileobj(original, output)


def verify_models(root: Path, month: str, source_commit: str) -> dict:
    report = {"source_commit": source_commit, "month": month, "models": {}}
    for market, prices_file in PRICE_FILES.items():
        state = root / "data/dashboard_ews" / market
        model = state / "models" / month
        card, _ = load_month(model)
        if (card["market"] != market or card["month"] != month or card["research"]
                or card["code_commit"] != source_commit):
            raise ValueError(f"Unexpected recovered model identity: {market}")
        if digest(root / "data/raw" / prices_file) != card["training_price_sha256"]:
            raise ValueError(f"Recovered training price checksum mismatch: {market}")
        marker = state / "legacy-manifest.json"
        if marker.exists():
            for name, expected in read_json(marker)["files"].items():
                if Path(name).name != name or digest(state / "legacy" / name) != expected:
                    raise ValueError(f"Recovered published history checksum mismatch: {market}")
        for path in sorted((state / "predictions").glob("*")):
            if path.is_dir() and not path.name.startswith("."):
                verify_prediction(path)
        report["models"][market] = {
            "id": card["id"], "cutoff": card["cutoff"], "created_at": card["created_at"],
            "files": {name: digest(model / name) for name in ("model.json", "model.sha256", "up.txt", "down.txt")}}
        print(f"Verified recovered model: {market} {month}, created={card['created_at']}, no retraining", flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-state-commit", required=True)
    parser.add_argument("--month", required=True)
    parser.add_argument("--state-dir", type=Path, default=Path(".dashboard-state"))
    args = parser.parse_args()
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=args.state_dir,
                          capture_output=True, text=True, check=True).stdout.strip()
    if base != args.expected_state_commit:
        raise RuntimeError("Recovery base has changed; refusing to overwrite a newer dashboard state")
    files = [path for path in args.artifact_dir.rglob("*") if path.is_file()]
    if len(files) != 1 or digest(files[0]) != args.artifact_sha256:
        raise ValueError("Downloaded artifact SHA-256 differs from the pinned training backup")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(files[0]) as outer:
            names = outer.namelist()
            if names != ["dashboard-state-backup.zip"]:
                raise ValueError("Unexpected training artifact contents")
            outer.extract("dashboard-state-backup.zip", root)
        packed = root / "packed"
        extract_backup(root / "dashboard-state-backup.zip", packed)
        # All writes are local restored data. The checked-out state branch is
        # retained until repacking and the final verified API ref update.
        restore_state(packed)
        report = verify_models(Path("."), args.month, args.source_commit)
    target = Path("outputs/recovery/model-verification.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
