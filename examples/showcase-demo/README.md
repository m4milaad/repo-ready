# Showcase Demo

This repository is a static fixture for the offline Repo X-Ray showcase.

## Setup

Use Python 3.12 and install the pinned dependency file:

```sh
python -m pip install -r requirements.txt
```

## Run

The demo is inspected as repository text; no project code is executed.

## Test

Run the documented test suite with:

```sh
tox
```

## Rebuild

The Dockerfile describes a Python 3.11 container for comparison with the
repository's Python 3.12 pin.
