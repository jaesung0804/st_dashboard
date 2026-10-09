# ai_stock_assistant

**[대시보드 공개 페이지 바로가기](https://jaesung0804.github.io/st_dashboard/)**

미국 투자회사 3개·9팀의 [시뮬레이션 화면](https://jaesung0804.github.io/st_dashboard/simulation/)과 [89개 실험·조직 운영 기록](docs/INVESTMENT_REPLAY_SESSION_20260910.md)을 추가했습니다. 직원별 전략·승인 근거와 공통 운영조정팀을 확인할 수 있습니다. 기존 [1차 설계](docs/INVESTMENT_REPLAY.md)와 [첫 실행 결과](docs/replays/2026-09-10-v1/report.md)도 보존합니다. 과거 연구용이며 별도 유료 AI API를 호출하지 않습니다.

한국·미국 주식 조기경보 대시보드입니다. 운영 모델은 **월 1회 학습 · 일 1회 추론 · 과거 예측 보존** 구조입니다.

- `Monthly Training`이 이전 달까지의 자료로 상승 기회·급락 위험 모델을 저장합니다. 같은 월의 재실행은 이미 저장된 모델을 유지합니다.
- `Daily Refresh KR`은 한국 시각 06:00, `Daily Refresh US`는 뉴욕 시각 17:30(장 마감 후)과 06:30(개장 전 재확인)에 실행합니다. 저장된 모델로 새 신호일만 추론하며 이전 예측은 보존합니다.
- 가격·거래량·시장 폭을 쓰는 작은 LightGBM과 로지스틱 회귀를 결합합니다. 날짜가 확실하지 않은 재무·거시 자료는 운영 입력에서 제외합니다.
- 상태 저장에 성공한 뒤 정적 HTML/JSON만 GitHub Pages에 공개합니다.

설계, 사건 정의, 한계, 재현 및 복구 절차는 [월별 조기경보 운영 문서](docs/monthly-ews.md)를 참고하세요. [개발 검증 기록](docs/validation/summary.md)에는 시간 순서를 분리한 한국·미국 점검 결과를 공개합니다. 과거 검증은 미래 성과를 보장하지 않습니다.

운영 환경은 `requirements-live.txt`를 사용합니다. 아래의 재무 수집·walk-forward 명령은 기존 연구용 경로이며 일 배치와 분리되어 있습니다.

## 재무자료 저장과 갱신

현재 종목 화면의 재무자료는 기존 Oracle DB의 기업별 기록에서 계산합니다. 재무 값·보고기간·공시 식별자·출처 해시·확인 시각은 DB에 저장하고 수정 이력도 보존합니다. 공개 페이지에는 계산된 비율과 자료 기준만 배포하며, DB 토큰이나 원본 재무제표를 보내지 않습니다.

`Collect dated financial statements`는 매일 한국 시각 02:20에 시장별 최대 600개 기업을 오래 확인하지 않은 순서로 갱신합니다. 모든 종목의 일일 갱신을 뜻하지 않으며, 실패·미제공 자료는 기존 성공 값을 지우지 않습니다. 기존 자료 이관은 같은 작업의 `bootstrap`, 특정 기업 재수집은 `refresh`의 `tickers` 입력으로 명시적으로 실행합니다. 최초 이관이 없는 환경에서는 빈 자료로 시작하지 않고 실패합니다.

기존 보관본은 검증된 `pipeline-state`에서 임시로 복원한 뒤 DB에 이관합니다. 신규 재무 수집은 원본 CSV나 Git 상태 브랜치를 누적하지 않습니다. 영구 보관된 과거 스냅샷은 복구 근거로 유지합니다. 화면의 `고정 실험 원본`은 기존 예측·시나리오·재무 의견을 그대로 보존하며 현재 재무지표 갱신은 모델을 재학습하지 않습니다.

재무지표의 기간은 지표별로 표시합니다. ROA·영업현금/자산은 확인된 최근 12개월 흐름과 현재·전년 동기 말 평균 자산으로 계산합니다. 한국은 전년도 연간 + 당해 누적 − 전년 동기 누적, 미국은 연속 네 분기를 합산합니다. 합산 자료가 부족하면 최근 연간 수치의 기준일을 따로 표시합니다. 유동비율은 같은 기말의 유동자산 ÷ 유동부채입니다. 공시일·결산일이 확인되지 않으면 이를 표시하고 과거 예측 입력으로 사용하지 않습니다. 계산할 수 없는 지표는 상세 화면에 누락 사유를 표시합니다.

`repair`는 외부 재수집 없이 기존 DB 전체의 지표를 다시 계산하고, 검증된 한국 원문 보관본에서 누락된 유동 항목·누적 흐름을 복원합니다. 기존 금액이나 공시 버전이 다른 원문으로는 덮어쓰지 않습니다. 새 공급원 조회가 필요한 항목은 `refresh`로 보충합니다.

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

The independent `daily-refresh-kr.yml` and `daily-refresh-us.yml` workflows are scheduled on weekdays at 06:00 Asia/Seoul and 06:30 America/New_York. US collection also runs at 17:30 America/New_York, after the regular close and provider-settlement buffer. This makes the completed session available without waiting until the next pre-open run. The US timezone automatically follows daylight saving time. GitHub scheduling can be delayed; special opening times are not calendar-adjusted.

Each market retries failed price requests/validation once before recording unavailable tickers. Corporate-action reconciliation may require a separate full-history request, also bounded to two attempts. The final decision requires observations for at least 95% of the recently active universe (the previous 21 observed sessions, bounded to 45 calendar days); yesterday's missing ticker remains in that denominator. Invalid or unavailable ticker updates retain their old history and are excluded from new inference, without fabricating null/zero price bars. Reports retain attempts, reasons, missing ticker counts and the final coverage decision. More than 5% missing, stale market data or an exhausted run budget prevents canonical replacement. Existing frozen predictions remain unchanged.

`daily-refresh.yml` remains a manual dispatcher (`all`, `kr`, or `us`) and recovery entry point:

```text
https://github.com/jaesung0804/st_dashboard/actions/workflows/daily-refresh.yml
```

Each market restores verified state, collects prices, infers new signal dates with frozen monthly models, and persists state **before** publishing `gh-pages`. Backend mode restores the external Oracle-backed `pipeline-state`; legacy mode retains `dashboard-state`. Manual `all` and monthly training use separate market transactions, with shared writer concurrency to prevent lost updates. One market's collection failure does not discard the other market's saved result. SQL row projection is secondary and its failure does not block a saved forecast's publication.

The [2026-09-11 market health review](docs/MARKET_HEALTH_REVIEW_20260911.md) explains the Korean pooled AUC versus same-date selection results, the US partially available session failure, and the remaining SQL migration work. Public aggregate evidence is linked from the report; raw prices and model binaries remain outside Git.

The production model uses causal price features. A separate daily `Collect dated financial statements` workflow refreshes Oracle financial records and public display metrics using the OpenDART secret; it does not change frozen model inputs or predictions. The commands above remain the legacy research collection path.

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
