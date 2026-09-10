# Research data access

When `RESEARCH_STORAGE=backend`, use `scripts/backend_records.py` for agent memory:

- Start with `list --kind <kind> --limit 20`, then `get` only the selected keys.
- Write one bounded JSON record using `put --expected-version <last-read-version>`; use 0 only for a new key. On HTTP 409, reread and merge the change.
- `agents`, `tasks`, `research`, `meetings`, `decisions`, `rounds`, `experiments`, `sources`, `policies`, and `checkpoints` are available record kinds.
- API URL and project token come from the private execution environment. Never put tokens or DB credentials in prompts, logs, code, browser JavaScript, or Git.
- Do not commit raw prices, retained source responses, photos, model binaries, runtime DBs, or full agent histories. Public dashboard summaries and source code remain ordinary repository artifacts.
- Restore `pipeline-state` before modifying model/collector state. Use the existing pack/unpack/push scripts; backend mode stores verified snapshots outside Git.
- Existing reference-document producers use `scripts/backend_references.py`: read each document first and publish related changes with one `write_many`. Oracle commits the document pointers and changed agent/task/round records together. On HTTP 409, stop and reread/merge all affected documents and records; never retry by replacing a fresh version blindly.
- A reference transaction accepts at most 10 JSON source artifacts (1 MiB each), 200 projected records (128 KiB each), and a 1 MiB request. Split growing documents by design before reaching these limits; do not silently truncate history or remove registered IDs.
- New fixed-roster replay, research import, and strategy-brief outputs go to the ignored `.research-backend/artifacts` cache and named immutable backend snapshots. Restore a saved output before continuing it; a snapshot head conflict stops publication. Keep logical evidence references and source hashes. Existing public summaries and historical bundles remain readable. Other experimental runners must be given an ignored output directory and archived explicitly before their results are referenced.
- Collection, training, and research execution must be explicit tasks. A read request must not start collection or training. Preserve existing point-in-time model rules and source timestamps.

Without backend configuration, preserve the existing storage behavior and do not silently switch to an empty database. A ChatGPT project conversation does not automatically configure the execution environment.
