# Accounting and retrospective history study (2026-09-06)

Production `ews-live-v1` forecasts remain immutable. The new reconstruction root
is `data/dashboard_research/reconstruction`. Missing dates are published from
that root with `predictionKind=reconstructed`; existing legacy and versioned
forecasts always take precedence. August simulations train through July 31;
September reuses the original September weights. Current retained prices and
listings are not a perfect historical point-in-time security master.

## Accounting inputs

A predeclared 128-company cohort per market is selected deterministically from
November-December 2022 liquidity, without selecting on later returns. SEC
companyfacts and OpenDART full statements preserve accession/receipt identifiers.
Values become usable the calendar day after actual filing, on the next eligible
market session. Amendments only enter subsequent as-of joins. Korean non-December
fiscal calendars are excluded until verified period handling is implemented.

Flows are assembled from nonoverlapping quarters; cumulative cash flow is not
summed four times. A fiscal annual duration or four contiguous quarters produces
trailing values. Missing fields remain missing. Inputs cover profitability,
cash conversion, accruals, working-capital growth, PP&E investment, liquidity,
liabilities, filing age, coverage and financial-sector applicability. Clipping
bounds, medians and scaling are fitted on the training subset only.

Customer concentration, one-off gains, full free cash flow and historical
valuation multiples require notes or dated shares/debt. They are not fabricated.
The public descriptions of TacozLabs/야근하는 회계사 videos (CRDO and TLN), and
its official curriculum informed the accounting questions, not ground-truth
labels or current valuations. Full video transcripts were not obtained.

## Matched experiment

Four arms share exactly the same dated company rows and outcomes: price,
price+macro, price+accounting, price+macro+accounting. The existing strengthened
shadow target is used: terminal five-market-session average +25%, excess +15pp
versus the observed market mean, at least four terminal days passing both.
Training samples occur every fifth observed market date. Purged train/calibration
splits precede each holdout month (2024-10, 2025-04, 2025-10, 2026-02). The last
month excludes outcomes not yet mature. These overlapping outcomes are not
independent investment trials. Pooled results must be read with month results.

The September experimental weights are persisted separately with SHA-256 and
can be applied by `scripts/infer_accounting_research.py`, without refitting.
It explicitly fails if that month's model is missing. This pilot does not
replace or automatically promote production recommendations.

The workflow `Accounting and history research` restores the official Korean
collection, collects SEC facts, compares the arms, verifies original hashes,
saves a recovery artifact, atomically persists state and publishes the report.
The public guide's `재무·확률 진단` tab shows the pilot and probability diagnosis.

## Interpretation

Probability dispersion is not a success metric. A nearly flat calibration map
can correctly reflect weak discrimination on its calibration sample; widening
it would not create information. September Korean compression and the August
model transition are measured separately. Calibration-fit diagnostics are
explicitly not independent test scores. August 2026 follow-up through September
4 is about one month, never a completed six-month backtest or executed strategy.
