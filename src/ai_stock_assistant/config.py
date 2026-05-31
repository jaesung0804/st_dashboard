from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
DAILY_OUTPUT_DIR = OUTPUT_DIR / "daily"


def ensure_project_dirs() -> None:
    for path in (RAW_DATA_DIR, PROCESSED_DATA_DIR, OUTPUT_DIR, DAILY_OUTPUT_DIR):
        path.mkdir(parents=True, exist_ok=True)
