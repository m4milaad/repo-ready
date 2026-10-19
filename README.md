# repo-ready

`repo-ready` is an offline Python CLI that scores how easy it is for a
newcomer to set up, run, test, and contribute to a local repository. It
supports Python and Node projects, reports a deterministic score from 0 to
100, explains failed checks, and safely scaffolds missing documentation and
configuration files without overwriting anything.

## Live demo

This project is a local CLI, so it has no hosted web demo. The reproducible
offline showcase below is the judge-facing demo. It uses the checked-in
deterministic demo repository, where all four Breakage Lab scenarios are
available:

```sh
repo-ready showcase examples/showcase-demo --output repo-report.html
```

Open `repo-report.html` directly in a browser, select a Breakage Lab scenario,
then inspect its evidence chain and Repair Preview. The Docker test run remains
the clean-room verification:

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
repo-ready check . --scope tracked --json
```

JSON output has stable keys and sorted checks/findings for CI. It is written to
standard output, so it can be saved or passed to another tool:

```sh
python -m repo_ready check ./some-repository --json --min-score 80 > readiness.json
```

Review and integration formats are also available:

```sh
repo-ready check . --format markdown
repo-ready check . --format sarif > readiness.sarif
```

SARIF contains one result for each failed or partial readiness check and keeps
repository-relative finding locations. Markdown is intended for pull-request
summaries; JSON remains the canonical structured report.

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

The stable readiness rule registry is also available for integrations:

```sh
repo-ready rules --json
```

### Repo X-Ray showcase

Generate a single, self-contained HTML report for an offline review. The
report contains the static readiness score, cross-file evidence graph,
precomputed Breakage Lab scenarios, create-only Repair Preview, and a
downloadable JSON evidence receipt. It never executes target code, invokes a
package manager, or writes the inspected repository.

```sh
repo-ready showcase . --output repo-report.html
```

Open `repo-report.html` directly from disk. It has no server, CDN, account, or
internet dependency. Scenario views are explicitly labelled `Simulated`; an
unavailable scenario explains which contract was not observed. To inspect the
JSON payload in a pipeline without writing HTML:

```sh
repo-ready showcase . --scope tracked --json > showcase-payload.json
```

The evidence receipt fingerprint is a deterministic SHA-256 of the canonical
static analysis, including its scenarios. It identifies the supplied evidence
snapshot; it is not a security certification and does not prove that the
repository builds.

### Release artifact inspection

Inspect a wheel or source distribution without extracting or executing it:

```sh
repo-ready artifact dist/repo_ready-0.1.0-py3-none-any.whl --project . --json
```

The artifact report checks member paths, links, duplicate members, size
limits, required Python distribution metadata, project/version parity, and
missing source modules. Artifact inspection is independent of readiness
scoring and uses its own versioned schema.

### Tracked-tree scope

When a repository scope must be distinguished from local generated files,
`tracked` reads the Git index directly and does not invoke Git or a shell:

```sh
repo-ready tracked . --json
```

The normal `check` command remains a filesystem scan. `tracked` is an explicit
scope report and identifies malformed or unavailable Git indexes rather than
silently treating them as a complete tracked tree.

`check --scope tracked` uses the same readiness report schema while restricting
the indexed files to paths recorded in the Git index. It fails clearly when a
readable Git index is unavailable.

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
repo-ready fix . --dry-run --format diff
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

`--format diff` renders the same create-only plan as a reviewable unified
diff. It is still a dry run and writes no files.

`fix` returns `0` for a valid repository, including a dry run, and `2` for an
invalid path or usage error.

## Test

The one project test command is:

```sh
python -m pytest -q
```

Tests use local fixture repositories and do not access the network.

The deterministic showcase gate can also be run directly:

```sh
python scripts/showcase_smoke.py
```

It checks all four Breakage Lab scenarios, packaged report assets, CLI report
generation, deterministic output, and that the demo repository remains
unchanged.

With optional Playwright browser tooling installed, run the visual gate against
the generated report as well:

```sh
python scripts/browser_smoke.py repo-report.html
```

The browser gate checks the interactive map, simulated scenario switching,
receipt download, keyboard-visible selection state, and narrow-screen layout.
It is separate from the standard test command because browsers are not a
runtime dependency.

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
