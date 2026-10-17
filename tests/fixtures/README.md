# Readiness report fixtures

Each fixture is a self-contained repository in `<case>/repo` and has a
deterministic `<case>/expected.json` report. The expected report uses
`"<REPO>"` for its path so copied temporary repositories can be compared
without host-specific values.

Cases:

- `perfect-python` — complete Python repository.
- `perfect-node` — complete Node repository.
- `empty-repo` — repository with no detectable ecosystem.
- `python-bad` — Python metadata, secret, path, runtime, and env failures.
- `node-bad` — Node metadata, secret, path, runtime, and env failures.
- `polyglot-mixed` — proportional credit for one good and one incomplete ecosystem.
- `edge-readme-variants` — alternate README, license, contributor, and template names.
- `edge-binary-and-large` — binary and over-limit files that must be skipped safely.
- `edge-symlinks` — links that must not be followed during scanning.
- `edge-env-usage` — documented and undocumented environment reads.
- `edge-docker` — version-tagged Docker base image and explicit `CMD`.
- `edge-non-utf8` — malformed UTF-8 source that remains scan-safe.
- `config-excludes` — `.repo-ready.toml` path exclusions.

The test suite is run with exactly:

```sh
python -m pytest -q
```
