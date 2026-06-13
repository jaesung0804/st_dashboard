# ai_stock_assistant

Korean stock early-warning research project for KOSPI/KOSDAQ.

The current operating model is a **walk-forward** workflow:

1. Build features from adjusted OHLCV and OpenDART financial statements.
2. Train separate LGBM models for:
   - upside warning: future 6-month return top 5% by date
   - downside warning: future 6-month return bottom 5% by date
3. For each signal date, train only on labels that would already be known.
4. Select candidates from upside top 5%, then exclude all non-GREEN downside-risk names.
5. Show the result in a local dashboard.

Do not interpret this as investment advice. Model performance still needs ongoing walk-forward validation before real-money use.

## Setup

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
