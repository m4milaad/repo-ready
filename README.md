# repo-ready

`repo-ready` is an offline Python CLI that scores whether a local repository is
ready for contributors and can be rebuilt from a clean environment. It checks
documentation, project metadata, templates, reproducibility files, and safe
configuration practices.

## Requirements

- Python 3.11 or newer
- `pytest` for the test suite

The application uses only the Python standard library at runtime. The test
dependency is pinned in `requirements.lock`.

## Setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
```

## Run

```sh
PYTHONPATH=src .venv/bin/python -m repo_ready check .
PYTHONPATH=src .venv/bin/python -m repo_ready check . --json
PYTHONPATH=src .venv/bin/python -m repo_ready fix . --dry-run
```

After installing the package, the equivalent console command is:

```sh
repo-ready check .
```

`check` prints a human-readable report by default or a machine-readable JSON
report with `--json`. `fix` scaffolds only missing files. It never overwrites
existing files; use `--dry-run` to preview planned changes.

## Test

```sh
make test
```

The tests use local fixture repositories and do not access the network.

## Deploy / rebuild

```sh
docker build -t repo-ready .
docker run --rm --network none repo-ready make test
```
