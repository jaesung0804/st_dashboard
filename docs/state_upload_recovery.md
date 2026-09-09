# Recover trained models after a state upload timeout

Monthly Training [34003362750](https://github.com/jaesung0804/st_dashboard/actions/runs/34003362750)
completed price collection, both September 2026 models, evaluation, packing and
the recovery upload. The final Git push failed three times with HTTP 408. Neither
`dashboard-state` nor `gh-pages` advanced. The immutable recovery artifact is
`9980415600`, with SHA-256
`1d127dd84e38ddb02290627592158467482d399c9fc9358c497402c3bcba3514`.

The old snapshot contains about 1.4 GB across 40,480 source files. Increasing the
timeout alone does not bound an individual large Git transfer. The new format:

- Gzips large files deterministically and divides them into at most 8 MiB blobs.
- Bundles the retained `opendart_accounts` and `yfinance_financials` directories
  as deterministic tar/gzip archives, preserving every relative path and byte.
- Keeps model files and archived forecasts individually accessible. Small files
  also receive SHA-256 checksums and Git attributes that preserve exact bytes.
- Verifies decompressed sizes/hashes, supports both prior raw split formats and
  the new encoding, and rejects invalid archive paths or symlinks.

State publication uses the GitHub Git Database API. Each blob is uploaded with a
bounded request, and its returned Git blob SHA is verified. Complete directory
trees are assembled bottom-up in bounded requests before the root commit is
created, avoiding server-side expansion of every slash-delimited path in one
request. The final update is a non-force fast-forward from the checked-out
parent; a concurrent state update aborts.
A timeout after the final request is reconciled against the remote commit SHA.
An incomplete upload therefore cannot expose a partial live state.

`Dashboard State Recovery` is pinned to the artifact, source commit and unchanged
state parent from the failed run. It verifies the outer artifact digest, restores
the state, checks model metadata/weights, training price digests and archived
forecast checksums, then repacks and publishes. It never calls collection or
training. The recovery report records the original model creation times and
file hashes. A successful recovery triggers `Daily Refresh` through `workflow_run`.
Rerunning recovery after state has advanced is deliberately refused.

Validation: all 40,480 retained source files round-tripped with identical SHA-256
hashes. They packed into 44 files, 274.2 MiB total, with no blob over 8 MiB; the
pack/restore/hash check took 36.7 seconds in the local validation environment.
Unit coverage includes interrupted uploads, a lost final response, concurrent
state changes, a truncated old Git tree, archive paths and exact file recovery.

`Publish verified GitHub Pages` follows a successful Daily Refresh. It explicitly
requests a build of the existing gh-pages source, waits for the matching commit,
and compares both public manifests with that commit's bytes. It does not modify
Pages settings. This additional build matters because a workflow's GITHUB_TOKEN
push alone does not trigger a Pages build, as documented by
[GitHub](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

References: [Git blobs](https://docs.github.com/en/rest/git/blobs),
[complete Git trees](https://docs.github.com/en/rest/git/trees),
[non-force reference updates](https://docs.github.com/en/rest/git/refs),
[artifact verification](https://github.com/actions/download-artifact).
