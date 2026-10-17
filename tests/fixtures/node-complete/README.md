# Complete Node fixture

This repository is a small, self-contained Node example used to exercise a
fully ready repository.

## Requirements

- Node.js 20.18.1

## Setup

Install the locked package tree with `npm ci`.

## Run

```sh
node index.js
```

## Test

```sh
npm test
```

## Deploy / rebuild

```sh
docker build -t complete-node-fixture .
docker run --rm complete-node-fixture
```

## Configuration

Copy `.env.example` when local configuration is needed.
