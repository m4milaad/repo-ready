# Contributing

## Development setup

Use Python 3.12, matching `.python-version` and the CI workflow. From the
repository root:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-build-isolation -e .
```

The editable install provides both the `repo-ready` console script and the
`python -m repo_ready` invocation.

For the optional browser smoke gate, install its locked Python dependencies and
Chromium, then install the locked Node browser tooling:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-browser.lock
.\.venv\Scripts\python.exe -m playwright install chromium
npm ci
```

The browser packages are development-only and are not needed for the standard
offline test suite.

## Verification

Run the one project test command before opening a pull request:

```sh
python -m pytest -q
```

For a CI-style readiness check, use JSON output and a minimum score:

```sh
python -m repo_ready check . --json --min-score 80
```

The readiness check exits `0` when the score meets its threshold, `1` when the
score is below it, and `2` for an invalid repository path or invalid command
usage. A `fix --dry-run --json` plan is available when reviewing safe
scaffolding changes without writing files.

## Changes

Keep checks deterministic, offline, and based only on the filesystem. Add or update
fixture repositories when detector behavior changes.
