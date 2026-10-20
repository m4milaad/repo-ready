# API and schema compatibility

`repo-ready` follows a conservative compatibility policy so release upgrades
do not silently break CI integrations.

## CLI exit codes

- `0`: the command completed successfully and any configured score threshold was met
- `1`: the repository was checked, but a score threshold or comparison reported a regression
- `2`: invalid path, invalid usage, invalid baseline, or configuration error

`scripts/verify.py` is intentionally separate from the installed static CLI.
It is an opt-in execution boundary, returns `0` only when the supplied or
detected command passes, returns `1` for a failed, timed-out, or unavailable
command, and returns `2` when verification cannot be configured.

## Stable report contracts

The following are versioned public outputs:

- `check --json`: readiness report fields and RR01–RR13 identifiers
- `diagnose --json`: contract graph `version`, evidence, edges, and diagnostics
- `showcase --json`: showcase `schema_version` and receipt fingerprint
- `artifact --json`: artifact inspection `version` and finding codes
- `tracked --json`: tracked-tree report fields and finding codes

Additive fields are preferred. Existing fields and identifiers are not removed
within a major schema version. A breaking schema change increments the relevant
schema version and is documented in `CHANGELOG.md`.

## Python API

The public exports from `repo_ready.__init__` include the context, scoring,
baseline, artifact, tracked-tree, contract-graph, showcase, and ecosystem
profile interfaces. Internal helpers in modules such as `checks.py` and
`contracts.py` are implementation details even when imported by the project’s
own tests.

## Release rules

Every release must pass `scripts/release_check.py` and
`scripts/release_gate.py`. The gate checks version parity, wheel and source
distribution safety, installed-wheel behavior, showcase behavior, and the
clean Docker suite before publication.
