from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_pages_deploy.py"


def write_date_file(root: Path, market: str, date: str = "2026-06-16") -> None:
    date_dir = root / "outputs" / f"lgbm_warning_dashboard_macro_{market}_latest" / "walkforward_scores_by_date"
    date_dir.mkdir(parents=True, exist_ok=True)
    (date_dir / f"{date}.json").write_text(json.dumps([]), encoding="utf-8")


def test_pages_deploy_succeeds_when_only_kr_dashboard_exists(tmp_path: Path) -> None:
    write_date_file(tmp_path, "kr")

    deploy_dir = tmp_path / "site"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--days", "1", "--deploy-dir", str(deploy_dir)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Skipping us pages" in result.stdout
    assert (deploy_dir / "lgbm_warning_dashboard_macro_kr_latest" / "dashboard.html").exists()
    assert not (deploy_dir / "lgbm_warning_dashboard_macro_us_latest").exists()
    assert not (deploy_dir / "down_negative_model_comparison").exists()

    home = (deploy_dir / "index.html").read_text(encoding="utf-8")
    dashboard = (deploy_dir / "lgbm_warning_dashboard_macro_kr_latest" / "dashboard.html").read_text(encoding="utf-8")
    stock = (deploy_dir / "lgbm_warning_dashboard_macro_kr_latest" / "stock.html").read_text(encoding="utf-8")

    for html in [home, dashboard, stock]:
        assert "themeToggle" in html
        assert "dashboardTheme" in html
        assert "down_negative_model_comparison" not in html
        assert "모델 비교" not in html


def test_pages_deploy_fails_when_no_dashboard_dates_exist(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--days", "1", "--deploy-dir", str(tmp_path / "site")],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "No dashboard date JSON files found under outputs" in result.stderr
