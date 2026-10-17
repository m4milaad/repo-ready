# Complete Python fixture

This repository is a small, self-contained Python example used to exercise a
fully ready repository.

## Requirements

- Python 3.11.11

## Setup

No third-party packages are required. The pinned lockfile is intentionally
empty apart from its explanatory comment.

## Run

```sh
python -m example_app
```

## Test

```sh
make test
```

## Deploy / rebuild

```sh
docker build -t complete-python-fixture .
docker run --rm complete-python-fixture
```

## Configuration

Copy `.env.example` when local configuration is needed.
