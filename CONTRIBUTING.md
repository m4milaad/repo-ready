# Contributing

## Development

1. Create a virtual environment with Python 3.12 or newer.
2. Install the locked test dependency with `python -m pip install -r requirements.lock`.
3. Run `python -m pytest -q` before opening a pull request.

## Changes

Keep checks deterministic, offline, and based only on the filesystem. Add or update
fixture repositories when detector behavior changes.
