# US collection: exceptions, recovery and retained history

The existing [state recovery workflow](state_upload_recovery.md) and monthly
model design establish three requirements: restore data without losing bytes, freeze monthly
models and historical predictions, and handle legitimate provider/trading
exceptions without repeatedly stopping the whole market batch.

The original month-long outage was a Windows CRLF split-state restoration error.
Subsequent KRX request amplification, unavailable intraday prices and oversized
state uploads were separate failures addressed by the preceding fixes. The US
collector changes below address remaining defects; they are not a claim that
those defects caused the original outage.

## Decisions

| Situation | Handling |
|---|---|
| Weekend/holiday with valid overlapping historical observations | Accept the provider's latest observed session; do not create a new date. |
| A few unavailable, suspended, delisted or incomplete-rebase tickers | Retain their existing internally consistent history, quarantine them from the new session and report unavailability. Missing observations are never filled with fabricated prices. |
| Entire market returns no valid observations | Fail collection before inference or CSV replacement. An existing frozen prediction does not make collection successful. |
| Fewer than 95% of the previously active tickers have provider rows on the latest session | Fail before replacing canonical prices. Activity uses the previous 21 retained market sessions, bounded to 45 calendar days, so one partial day does not immediately shrink the denominator. |
| A ticker has fallen weeks behind the rest of the market | Start its request from its own last retained observation minus the overlap. Group equal start dates into batches. |
| Overlapping close or adjusted-close prices change materially | Obtain all retained dates for that ticker on the provider's new scale before merging. If the complete rebase cannot be verified, quarantine only that ticker; the 95% market-coverage guard still rejects widespread failures. |
| CSV write fails or is interrupted | Write a sibling temporary file, flush it, then atomically replace the destination only when the write succeeds. |

The default ten-calendar-day overlap remains a correction window. It no longer
defines the maximum recoverable gap for every ticker in the market. Response
validation checks ticker identity, query dates, duplicate keys and price/volume
values. Entirely unavailable intraday OHL values are retained as missing values;
close and volume remain usable, matching the existing model's range masking.

Adjustment detection uses relative tolerance 0.01% and absolute tolerance
0.000001 for both close and adjusted close. Ordinary floating-point noise should
not trigger full-history downloads; changes such as dividends can do so. This
updates retained market data, not previously generated forecasts or frozen
monthly model artifacts. Model performance changes are not claimed by this fix.

## Bounded work and diagnostics

Each yfinance download has at most eight workers and a 15-second response
timeout. A missing active ticker is retried once; unavailable inactive tickers
are not repeatedly retried in the same run. Requests are grouped by required
start date, in batches of at most 100 tickers. Repeated batch failures stop early
when the allowed active-universe missingness has already been exceeded.

The scheduling budget defaults to one hour. It is checked between downloads and
before accepting the result; it cannot forcibly interrupt a library call already
in progress. The workflow's existing 120-minute step timeout remains the final
bound. Unlike the KRX collector, US responses are not checkpointed across runs;
a failed run may re-download successful batches. This change does not invent a
new authoritative state or alter the existing state-before-Pages publication
order.

`outputs/daily/<requested-date>/us_daily_data_refresh_summary.csv` records each
ticker's requested start, response status and error. `us_collection.json` records
coverage, unavailable/rebased tickers and the validation decision. Both files
are included in the workflow's existing collection-diagnostics artifact.

## Remaining limits

- The inherited seven-calendar-day freshness allowance is retained for holidays.
  It is not a complete exchange calendar: a provider returning a plausible but
  old nonempty series within that window can still be accepted. This change
  fixes the demonstrated empty/failed download success path, without treating
  every non-trading day as a failure.
- Existing internal historical holes whose ticker has already resumed are not
  automatically distinguished from real trading suspensions. No weekday bars
  are synthesized, and historical forecasts are not regenerated.
- Up to 5% missingness is an explicit operational tradeoff, not a claim that
  unavailable tickers are delisted. Their last observations remain in the raw
  history and their absence is recorded in diagnostics.
- A detected adjustment with an incomplete full-history response is never
  merged. The old internally consistent history is retained and the ticker has
  no row on the new signal date, so it is excluded from that day's inference.
  The market batch still fails when such exceptions or other missing data push
  current-session coverage below 95%.

## Regression checks

The three existing tests that read generated UTF-8 JSON with the Windows cp949
default now specify UTF-8 explicitly; their assertions are unchanged. These were
local test-environment failures, not the cause of the production batch outage.

`tests/test_us_incremental.py` exercises complete outages, partial failures,
holidays, retained delisted history, long ticker-specific gaps, split/dividend
rebases, truncated responses, invalid rows, missing intraday quotes, bounded
download settings and interrupted writes. Run with the workflow's UTF-8 setting:

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pytest -q
```

The current local package environment and an isolated environment installed from
`requirements-live.txt` are checked separately; installing the latter does not
change the user's existing virtual environment.

On September 6, 2026, the full suite passed 129 tests in both environments.
The 22 US collector regression cases are included in that total. The repaired
JSON-reading tests also passed under the default Windows locale without enabling
UTF-8 mode.

A real Yahoo smoke check used disposable copies of the retained AAPL, MSFT and
BRK-B histories, ending June 15. All three advanced to September 4 with 100%
latest-session coverage (3,798 to 3,969 total rows). AAPL and MSFT required full
history rebases, exercising the adjustment path with actual provider responses.
The original US CSV's SHA-256 was identical before and after the check. This is
a three-ticker integration check, not a new full-universe production batch or a
new model-performance evaluation.

The yfinance [download API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
documents the bounded-thread and response-timeout parameters and the exclusive
end date used by the adapter.
