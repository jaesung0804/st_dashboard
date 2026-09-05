# Resumable KRX price collection

The first monthly run at commit `5e3ab57` exhausted the 330-minute job budget
while collecting the gap after 2026-08-05. KRX bulk queries failed and the
fallback repeated the entire universe once for every missing date. The final
trace was inside pykrx's Naver HTTP request.

The corrected daily/monthly collector:

- Uses the **restored universe**, without repeatedly discovering listings per
  day. IPO/universe discovery is a separate operation; this patch does not claim
  to refresh the listing universe.
- Requests each ticker's entire missing range, starting at its own last quote
  minus a 10-calendar-day correction overlap (pipeline default). Thus one
  month's gap normally requires one price request per ticker, not per day.
- Reads the same adjusted Naver chart series used by pykrx. HTTPS, a 5-second
  connect timeout, a 15-second read timeout, at most two attempts, four workers,
  and a shared 0.25-second request interval bound ordinary provider failures.
  These socket timeouts are not a total download deadline; the collector also
  has a one-hour scheduling budget and the workflow a 120-minute step limit.
- Stops after eight consecutive provider failures. Any failed ticker prevents
  replacing canonical prices and therefore prevents subsequent model training
  or publication. Successful ticker checkpoints survive for a retry.
- Validates schema, dates, ticker identity, finite OHLCV, duplicate keys,
  freshness (at most seven calendar days), and latest-session coverage (95% of
  the previously active restored universe). A weekend does not create a price
  row. Seven days allows market holidays; it is not an exchange-calendar proof
  that no completed trading session is missing.
- Re-fetches a ticker's full retained history if overlapping adjusted prices
  changed materially, avoiding a mixed pre-/post-split price scale. Full history
  must be returned before that rebase is accepted. Published predictions and
  frozen monthly model artifacts are never rewritten by price collection.
- Stages the final CSV beside its destination, then atomically replaces it only
  after validation. Existing out-of-universe/delisted history is retained.

Checkpoints are checksummed, query/version-bound gzip JSON files under
`data/cache/krx_ranges`, valid for 36 hours. GitHub Actions restores/saves them
and retains collection diagnostics/checkpoints as a seven-day artifact. They
are a performance cache, not the authoritative `dashboard-state` branch. An
empty cache or a failed cache restore causes re-fetching, not missing data.

The original run cannot be rerun to execute new code: a rerun uses its original
commit. Use **Monthly Training -> Run workflow -> main -> all** after the fix,
or the existing scoped push trigger when `monthly-training.yml` changes. A
successful monthly run still triggers Daily Refresh through `workflow_run`.
The model, archived predictions, state-before-Pages order and 330-minute job
limit are unchanged.

References: [pykrx Naver parser](https://github.com/sharebook-kr/pykrx/blob/master/pykrx/website/naver/wrap.py),
[Naver endpoint used by pykrx](https://github.com/sharebook-kr/pykrx/blob/master/pykrx/website/naver/core.py),
[Requests timeout semantics](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts).
