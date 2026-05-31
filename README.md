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
.\.venv\Scripts\python.exe scripts\run_walkforward_warning.py --start 2025-12-01 --end 2026-05-31
```

Build the dashboard from walk-forward output:

```powershell
.\.venv\Scripts\python.exe scripts\build_walkforward_dashboard.py
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
http://127.0.0.1:8765/dashboard.html
```

## Validation

The walk-forward script writes:

- `outputs/walkforward_warning/walkforward_candidates.csv`
- `outputs/walkforward_warning/walkforward_validation.csv`

Each signal date uses a label cutoff of approximately `signal_date - 126 trading days`, so future 6-month returns are not used for training that signal date.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```
