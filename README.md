# repo-ready

`repo-ready` is an offline Python CLI that scores how easy it is for a
newcomer to set up, run, test, and contribute to a local repository. It
supports Python and Node projects, reports a deterministic score from 0 to
100, explains failed checks, and safely scaffolds missing documentation and
configuration files without overwriting anything.

## Requirements

- Python 3.12 or newer
- Docker for the clean-image verification command

The runtime uses only the Python standard library. `pytest` is the sole
development dependency and is pinned in `requirements.lock`.

## Setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation -e .
```

## Run

```sh
repo-ready check .
repo-ready check ./some-repository --json
repo-ready check . --min-score 80 --no-color
repo-ready --version
```

The path defaults to the current directory. JSON output has stable keys and
sorted checks/findings for CI. The exit status is 1 when `--min-score` (or the
`min_score` value in `.repo-ready.toml`) is not met, and 2 for an invalid path
or usage error.

The checks are RR01 through RR13: README, LICENSE, CONTRIBUTING, issue and
pull-request templates, lockfile, pinned runtime, Dockerfile, test command,
environment example, machine-specific paths, `.gitignore`, and CI workflow.
Their fixed weights sum to 100. Checks that do not apply earn their full
weight and are shown as `na`.

## Fix

```sh
repo-ready fix . --dry-run
repo-ready fix .
repo-ready fix . --license mit --holder "Project Authors" --runtime-version 3.12
```

`fix` creates only missing files, using exclusive-create mode. It never
overwrites an existing file, follows a symlink outside the repository, reads
the interpreter's runtime version, or runs a package manager. Lockfiles, test
commands, and machine-specific paths are reported as manual work. A dry run
writes nothing.

## Test

The one project test command is:

```sh
python -m pytest -q
```

The Makefile target is only an alias for that command. Tests use local fixture
repositories, guard sockets and subprocesses, and do not access the network.

## Rebuild and offline verification

```sh
docker build -t repo-ready .
docker run --rm --network none repo-ready python -m pytest -q
docker run --rm -v "$PWD":/work repo-ready repo-ready check /work --json
```

The Docker image has no Make dependency and its default command is
`python -m pytest -q`. `.dockerignore` keeps local virtual environments,
caches, and build artifacts out of the build context.

## Configuration

An optional `.repo-ready.toml` at the repository root supports:

```toml
exclude = ["generated", "tests/fixtures"]
min_score = 80
```

The default scan excludes VCS directories, virtual environments, package
vendor directories, caches, build output, and files over 1 MiB or identified as
binary. It never executes the target repository.
