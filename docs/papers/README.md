# Public paper library

`library.json` is the only content input for `/papers/`. It contains public,
reviewed research notes, never account balances, holdings, orders, personal
details, private notes, credentials or retained source responses. Unknown fields
fail the build. This is a public presentation artifact, not an agent-memory or
backend-reference document. Research execution and data collection are separate.

## Schema version 1

The root has exactly `schema_version` (1), `updated_at` (ISO date) and `papers`.
Every paper has the following required fields (only the two optional fields below are also accepted):

| Field | Type / meaning |
| --- | --- |
| `id` | Unique stable lowercase letters/digits/hyphens, at most 80 characters |
| `title` | Original paper title, plain text |
| `authors` | List of public author names; empty means unconfirmed |
| `year` | Integer publication year, not later than the current year |
| `field` | One public subject label; used in the field filter |
| `tracks` | One or both of `mechanical`, `long_term`; models remain independent |
| `review_status` | `metadata` (bibliography), `abstract`, `partial_text`, or `full_text` |
| `source_url` | HTTPS original paper/publisher/working-paper URL; no credentials |
| `research_question` | Plain text or a list of paragraphs |
| `core_math` | Nonempty list of `{formula, intuition}` plain-text pairs |
| `empirical_results` | Plain text or paragraphs; use source-backed results |
| `limitations` | Plain text or paragraphs |
| `application_hypothesis` | Plain text or paragraphs; label proposed applications as hypotheses |
| `validation_method` | Plain text or paragraphs; distinguish completed vs proposed checks |

Optional fields: `publication` (venue/version label), `review_note` (exact reviewed sections/version).

For an unavailable section, explicitly state what has not been reviewed. Do not
infer results from a title or abstract. `partial_text` identifies a limited original-text review. `full_text` means the original text was
reviewed; it does not mean replication, peer review, or a validated trading rule.
Record page/section/table references directly in the relevant prose. Formulas
are rendered as safe plain text (Unicode mathematical notation is supported),
without external rendering services. Limit: 1 MiB source, 200 papers, 30 paragraphs
per section, 12 formula/intuition pairs. Split a growing library by design.

## Local build and checks

```
python scripts/build_paper_library.py --destination .work/paper-preview/papers
python -m pytest tests/test_paper_library.py tests/test_build_pages_deploy.py
node tests/test_paper_ui.cjs
```

The initial catalog is eight selected AI papers, with review scope supplied by the research task and bibliographic metadata checked against the linked original sites on 2026-10-11. Quantitative results and equations not yet supplied are explicitly marked pending. Proposed validation is never reported as completed.

The normal Pages builder includes this library. The UI-refresh workflow watches
the builder, shared theme, assets and `docs/papers/**`. It restores frozen state
and exports presentation data without training. Publication still uses the
existing serialized workflow and `gh-pages` overlay. A content edit should be
reviewed before committing, because this directory is public source code.

Filter URL keys: `q`, `year`, `field`, `track`, `review`; paper deep links use
`#paper=<id>`. Native dialogs support keyboard focus, Escape, and return focus.
The `dashboardTheme` localStorage key preserves explicit `light`/`dark` choices.
A missing, invalid or unavailable preference defaults to dark before styles load.
