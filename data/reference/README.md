# FRED-MD bootstrap features

`fred_md_2026_07_features.json` is a 74 KB derived snapshot of 79 official monthly
FRED-MD vintages, 2020-01 through 2026-07, retrieved on 2026-09-06. It contains the
ten model inputs, the original CSV URL and SHA256, the observation months, and the
conservative availability date for each vintage. No keys, stock predictions, or
privately licensed third-party series are included.

Source: https://www.stlouisfed.org/research/economists/mccracken/fred-databases

Historical files were read from the linked 2015-01–2025-12 ZIP; 2026 files were
downloaded individually from the linked monthly CSVs. The transformation is
`ai_stock_assistant.data.macro_vintages.vintage_features`. A SHA256 sidecar verifies
the snapshot itself. The corresponding uncompressed CSV archive is intentionally
not copied into the app repository or Pages bundle.

This bootstrap avoids repeatedly downloading the 35 MB history from GitHub
runners, where the initial archive request timed out. Only missing, already
date-eligible vintages are restored. Existing cached rows are never replaced.
New months still require real official CSV downloads; a provider failure is an
error, not permission to invent or relabel old data as current.
