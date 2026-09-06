# FRED-MD bootstrap features

`fred_md_2026_08_features.json` is a 75 KB derived snapshot of 80 official monthly
FRED-MD vintages, 2020-01 through 2026-08, retrieved on 2026-09-06. It contains the
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

The original 79-vintage July snapshot remains for provenance. The August vintage
was also downloaded and checked from the official CSV. It cannot enter September
signals: its conservative available date is 2026-10-01, with strict-prior joining
making it usable only from 2026-10-02. It prepares the October update without a
fresh network request. Later vintages still need verified downloads; HTTP requests
from GitHub runners were timing out in both the standard and browser-header probes.
