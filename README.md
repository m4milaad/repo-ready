# repo-ready

`repo-ready` is an offline Python CLI that scores how easy it is for a
newcomer to set up, run, test, and contribute to a local repository. It
supports Python and Node projects, reports a deterministic score from 0 to
100, explains failed checks, and safely scaffolds missing documentation and
configuration files without overwriting anything.

## Live demo

This project is a local CLI, so it has no hosted web demo. The reproducible
Docker test run below is the judge-facing demo:

```sh
docker build -t repo-ready .
docker run --rm --network none repo-ready python -m pytest -q
```

## Requirements

- Python 3.12
- Docker for the clean-image verification command

The runtime uses only the Python standard library. `pytest` and its locked
test-tool dependencies are development dependencies listed in
`requirements.lock`.

## Setup

### Local installation

From a checkout of `repo-ready`, create a Python 3.12 virtual environment,
install the locked development tools, and install the package in editable
mode:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-build-isolation -e .
```

The editable install makes both invocation styles available:

```sh
repo-ready check .
python -m repo_ready check .
```

`repo-ready` is the installed console script. `python -m repo_ready` invokes
the same CLI through the package module. Both commands default to the current
directory when no repository path is supplied.

CI and Docker use a non-editable wheel install so their test runs verify the
packaged application rather than importing directly from `src/`. The test
suite normally adds `src/` for convenient local checkout testing; set
`REPO_READY_TEST_NO_SRC=1` when checking an installed artifact.

## Run

### Check a repository

```sh
repo-ready check .
repo-ready check ./some-repository --json
repo-ready check . --min-score 80 --no-color
repo-ready diagnose . --json
```

JSON output has stable keys and sorted checks/findings for CI. It is written to
standard output, so it can be saved or passed to another tool:

```sh
python -m repo_ready check ./some-repository --json --min-score 80 > readiness.json
```

`--min-score` sets the minimum accepted score for that invocation. If the
target repository has `min_score` in `.repo-ready.toml`, the command-line value
takes precedence. The report is still emitted as JSON when the score is below
the threshold; the exit status communicates the result to CI.

### Cross-file contract diagnostics

`diagnose` builds a separate, static evidence graph from README commands,
Makefile recipes, manifests, lockfiles, runtime pins, CI workflows, and the
root Dockerfile. It reports contradictions such as a CI runtime that differs
from `.python-version` or a README test command that differs from the
effective Makefile command. It does not execute the repository, change the
100-point readiness score, or add graph data to `check --json`.

```sh
repo-ready diagnose .
repo-ready diagnose ./some-repository --json > contract-graph.json
```

The graph has its own versioned schema with `evidence`, `edges`, and
`diagnostics` collections. A diagnostic is static evidence, not proof that a
command succeeds when executed.

### Baseline comparison

Compare a current report with a previously saved `check --json` report. The
comparison ignores checkout paths and reports check-level changes even when
the total score is unchanged:

```sh
repo-ready check . --json > readiness-before.json
repo-ready compare . --baseline readiness-before.json --json
```

The comparison output has its own versioned schema. It exits `1` when the
score or check evidence regresses, `0` when there is no regression, and `2`
when the baseline is missing or invalid.

### Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | The repository was checked and its score met the configured threshold, or no threshold was configured. |
| `1` | The repository was checked, but its score was below `--min-score` or `.repo-ready.toml`'s `min_score`. |
| `2` | The repository path is invalid or unreadable, or command-line usage is invalid. |

### GitHub Actions

The following workflow is a copyable CI example. It runs the required project
test command and then fails the job if readiness is below the selected score:

```yaml
name: Repository readiness

on:
  push:
  pull_request:

jobs:
  readiness:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install the locked tools
        run: |
          python -m pip install -r requirements.lock
      - name: Build and install the wheel
        run: |
          python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/repo-ready-wheel .
          python -m pip install --no-index --force-reinstall --no-deps /tmp/repo-ready-wheel/*.whl
      - name: Run installed-package tests
        env:
          PYTHONPATH: ''
          REPO_READY_TEST_NO_SRC: '1'
        run: |
          python -m pip check
          python -c "from pathlib import Path; import repo_ready; package = Path(repo_ready.__file__).resolve(); assert Path.cwd() / 'src' not in package.parents, package"
          python -m pytest -q
      - name: Check repository readiness
        run: python -m repo_ready check . --json --min-score 80
```

The checks are RR01 through RR13: README, LICENSE, CONTRIBUTING, issue and
pull-request templates, lockfile, pinned runtime, Dockerfile, test command,
environment example, machine-specific paths, `.gitignore`, and CI workflow.
Their fixed weights sum to 100. Checks that do not apply earn their full
weight and are shown as `na`.

## Fix

```sh
repo-ready fix . --dry-run
repo-ready fix . --dry-run --json
repo-ready fix .
repo-ready fix . --license mit --holder "Project Authors" --runtime-version 3.12
```

`fix` creates only missing files, using exclusive-create mode. It never
overwrites an existing file, follows a symlink outside the repository, reads
the interpreter's runtime version, or runs a package manager. Lockfiles, test
commands, and machine-specific paths are reported as manual work. A dry run
writes nothing. For automation, `--json` returns the operation list and
separate `created`, `would_create`, `skipped_existing`, `skipped_unsafe`, and
`manual` collections. In a dry run, `created` is empty and proposed files are
listed in `would_create`:

```sh
python -m repo_ready fix . --dry-run --json > fix-plan.json
```

`fix` returns `0` for a valid repository, including a dry run, and `2` for an
invalid path or usage error.

## Test

The one project test command is:

```sh
python -m pytest -q
```

Tests use local fixture repositories and do not access the network.

## Rebuild and offline verification

```sh
docker build -t repo-ready .
docker run --rm --network none repo-ready python -m pytest -q
docker run --rm -v "$PWD":/work repo-ready repo-ready check /work --json
```

The Docker image has no Make dependency and its default command is
`python -m pytest -q`. It installs the project as a wheel-equivalent
non-editable package and disables the test suite's checkout-source path
injection. `.dockerignore` keeps local virtual environments, caches, and build
artifacts out of the build context.

To check a different local repository, build the image from the `repo-ready`
checkout and mount the target repository at `/work`:

```sh
docker run --rm --network none \
  -v "/path/to/another-repository":/work:ro \
  repo-ready repo-ready check /work --json --min-score 80
```

The target is read-only and is scanned in the container; no package manager or
target-repository command is run by `repo-ready`.

## Configuration

An optional `.repo-ready.toml` at the repository root supports:

```toml
exclude = ["generated", "tests/fixtures"]
min_score = 80
```

The default scan excludes VCS directories, virtual environments, package
vendor directories, caches, build output, and files over 1 MiB or identified as
binary. It never executes the target repository.
