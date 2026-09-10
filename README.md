# ai_stock_assistant

미국 투자회사 3개·9팀의 [시뮬레이션 화면](https://jaesung0804.github.io/st_dashboard/simulation/)과 [89개 실험·조직 운영 기록](docs/INVESTMENT_REPLAY_SESSION_20260910.md)을 추가했습니다. 직원별 전략·승인 근거와 공통 운영조정팀을 확인할 수 있습니다. 기존 [1차 설계](docs/INVESTMENT_REPLAY.md)와 [첫 실행 결과](docs/replays/2026-09-10-v1/report.md)도 보존합니다. 과거 연구용이며 별도 유료 AI API를 호출하지 않습니다.

한국·미국 주식 조기경보 대시보드입니다. 운영 모델은 **월 1회 학습 · 일 1회 추론 · 과거 예측 보존** 구조입니다.

- `Monthly Training`이 이전 달까지의 자료로 상승 기회·급락 위험 모델을 저장합니다. 같은 월의 재실행은 이미 저장된 모델을 유지합니다.
- `Daily Refresh`는 저장된 모델로 새 신호일만 추론합니다. 이전 예측은 다시 계산하거나 덮어쓰지 않습니다.
- 가격·거래량·시장 폭을 쓰는 작은 LightGBM과 로지스틱 회귀를 결합합니다. 날짜가 확실하지 않은 재무·거시 자료는 운영 입력에서 제외합니다.
- 상태 저장에 성공한 뒤 정적 HTML/JSON만 GitHub Pages에 공개합니다.

설계, 사건 정의, 한계, 재현 및 복구 절차는 [월별 조기경보 운영 문서](docs/monthly-ews.md)를 참고하세요. [개발 검증 기록](docs/validation/summary.md)에는 시간 순서를 분리한 한국·미국 점검 결과를 공개합니다. 과거 검증은 미래 성과를 보장하지 않습니다.

운영 환경은 `requirements-live.txt`를 사용합니다. 아래의 재무 수집·walk-forward 명령은 기존 연구용 경로이며 일 배치와 분리되어 있습니다.

## Research setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Data Collection

Fetch KRX listings and OHLCV:

```powershell
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-kr-listings
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-kr-universe-prices --markets KOSPI KOSDAQ
```

Fetch US listings and yfinance OHLCV:

```powershell
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-us-listings
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-us-profiles --listings-path data\raw\us_listings_nasdaq_nyse_20260612.csv --output-path data\raw\us_listings_nasdaq_nyse_yfinfo_20260612.csv --workers 12 --sleep 0
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-us-prices --tickers AAPL MSFT NVDA --start 20210101 --end 20260611
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-us-universe-prices --markets NASDAQ NYSE --limit 50
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-us-financials --listings-path data\raw\us_listings_nasdaq_nyse_20260611.csv --limit 50
```

Build the S&P 500 US walk-forward dashboard using the same date window as the Korean run:

```powershell
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-us-listings --universe sp500 --asof 20260608
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-us-universe-prices --listings-path data\raw\us_listings_sp500_20260608.csv --start 20210531 --end 20260608 --batch-size 25
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-us-financials --listings-path data\raw\us_listings_sp500_20260608.csv --workers 8 --sleep 0
.\.venv\Scripts\python.exe -c "from pathlib import Path; from ai_stock_assistant.features import build_feature_matrix; build_feature_matrix(Path('data/raw/us_ohlcv_sp500_20210531_20260608.csv'), Path('data/raw/yfinance_financials_annual_quarterly.csv'), Path('data/raw/us_listings_sp500_20260608.csv'), Path('outputs/us_features_sp500/training_features_daily.csv'))"
.\.venv\Scripts\python.exe scripts\run_walkforward_warning.py --market us --features-path outputs\us_features_sp500\training_features_daily.csv --listings-path data\raw\us_listings_sp500_20260608.csv --out-dir outputs\walkforward_warning_us --frequency daily --min-trading-value 5000000 --min-close 1
.\.venv\Scripts\python.exe scripts\build_walkforward_dashboard.py --wf-dir outputs\walkforward_warning_us --out-dir outputs\lgbm_warning_dashboard_us
```

Fetch OpenDART data:

```powershell
$env:OPENDART_API_KEY="your-key"
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-opendart-corp-codes
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli fetch-kr-financials --listings-path data\raw\krx_listings_kospi_kosdaq_20260531.csv --years 2021 2022 2023 2024 2025 --reports annual q1 half q3 --workers 8
```

Combine annual and quarterly financial files:

```powershell
.\.venv\Scripts\python.exe -m ai_stock_assistant.cli combine-kr-financials --listings-path data\raw\krx_listings_kospi_kosdaq_20260531.csv --account-paths data\raw\opendart_accounts_annual_2021_2025.csv data\raw\opendart_accounts_q1_half_q3_2021_2025.csv --manifest-paths data\raw\opendart_manifest_annual_2021_2025.csv data\raw\opendart_manifest_q1_half_q3_2021_2025.csv --output-slug all_reports_2021_2025
```

## Daily Refresh

GitHub Pages deployment is updated by `.github/workflows/daily-refresh.yml`.
The workflow runs on a schedule and can also be started manually from the Actions page:

```text
https://github.com/jaesung0804/st_dashboard/actions/workflows/daily-refresh.yml
```

The runner restores `dashboard-state`, preserves published history, refreshes prices for the selected market (`all`, `kr`, or `us`), and infers only new signal dates with frozen monthly models. It persists state **before** publishing `gh-pages`. Training runs separately in `monthly-training.yml`. Both workflows share a concurrency group.

The production model uses causal price features; existing financial files are retained but are not refreshed by the daily production workflow. OpenDART secrets remain relevant only to the research collection commands.

Build the lightweight Pages bundle locally:

```powershell
.\.venv\Scripts\python.exe scripts\build_pages_deploy.py --days 22
```

```powershell
$env:OPENDART_API_KEY="your-key"
.\scripts\daily_refresh.ps1
```

Generated data is intentionally ignored by git:

- `data/raw/`
- `data/processed/`
- `outputs/`
- `models/`
- `reports/`

## Walk-Forward Model

Generate leak-safe month-end candidates:

```powershell
.\.venv\Scripts\python.exe scripts\run_walkforward_warning.py --frequency daily
```

Build the dashboard from walk-forward output:

```powershell
.\.venv\Scripts\python.exe scripts\build_walkforward_dashboard.py
```

For large daily histories, build only the most recent signal dates for the web dashboard:

```powershell
.\.venv\Scripts\python.exe scripts\build_walkforward_dashboard.py --recent-days 260
```

Generate text summaries for PDF or chat sharing:

```powershell
.\.venv\Scripts\python.exe scripts\generate_walkforward_summaries.py
```

Serve the dashboard locally:

```powershell
.\.venv\Scripts\python.exe scripts\serve_lgbm_dashboard.py --port 8765
```

Open:

```text
http://127.0.0.1:8765/index.html
```

## Validation

The walk-forward script writes:

- `outputs/walkforward_warning/walkforward_scores.csv`
- `outputs/walkforward_warning/walkforward_candidates.csv`
- `outputs/walkforward_warning/walkforward_up_candidates.csv`
- `outputs/walkforward_warning/walkforward_down_red.csv`
- `outputs/walkforward_warning/walkforward_validation.csv`

Each signal date uses a label cutoff of approximately `signal_date - 126 trading days`, so future 6-month returns are not used for training that signal date. The dashboard writes a small `manifest.json` plus one JSON file per included signal date under `outputs/lgbm_warning_dashboard/walkforward_scores_by_date/`, so the browser only loads the selected date instead of the full multi-year score table. Use `--recent-days 0` to include every signal date, or `--copy-csv` if CSV exports are needed in the dashboard folder.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```
