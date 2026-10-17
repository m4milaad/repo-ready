# Detector fixtures

These directories are small, deterministic local repositories for the future
repository-readiness detector. They do not require network access or any
machine-specific paths, and they intentionally do not contain pytest tests.

## Repositories

- `python-complete/` — a complete Python repository with every check enabled.
- `python-minimal/` — a recognizable but intentionally incomplete Python
  repository.
- `node-complete/` — a complete Node repository with every check enabled.
- `empty-incomplete/` — an empty repository containing only its fixture
  expectation.

Each repository has an `expected.json` file describing the future detector
contract:

```json
{
  "score": 100,
  "ecosystems": ["python"],
  "checks": {
    "readme_sections": true,
    "license": true,
    "contributing": true,
    "templates": true,
    "lockfile": true,
    "pinned_runtime": true,
    "dockerfile": true,
    "test_command": true,
    "env_example": true,
    "no_hardcoded_paths": true
  }
}
```

The score is the sum of the weights for enabled checks:

| Check | Weight |
| --- | ---: |
| `readme_sections` | 15 |
| `license` | 10 |
| `contributing` | 10 |
| `templates` | 10 |
| `lockfile` | 10 |
| `pinned_runtime` | 10 |
| `dockerfile` | 10 |
| `test_command` | 10 |
| `env_example` | 5 |
| `no_hardcoded_paths` | 10 |

The ten check keys are fixed across all expectations. Fixture paths in the
repository contents are relative, so the fixtures can be copied and run from
any checkout location.
