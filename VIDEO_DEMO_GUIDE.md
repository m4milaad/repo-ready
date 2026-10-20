# repo-ready video demo guide

This is a copy-and-paste runbook for recording a short demonstration of
`repo-ready` against any local repository. The showcase is offline and static:
it reads repository files, never runs target code, and does not install target
dependencies or modify the target repository.

## What the viewer will see

The strongest story is:

1. Run a normal readiness check and show the score.
2. Open the cross-file contract diagnosis and reveal a contradiction.
3. Generate the self-contained Repo X-Ray report.
4. Select a **Simulated** Breakage Lab scenario.
5. Follow the evidence chain and inspect the Repair Preview.
6. Download the evidence receipt and explain its limitations.

The report is a single HTML file. It needs no server, CDN, account, or
internet connection.

## One-time setup

Run these commands from the `repo-ready` checkout. Python 3.12 is the pinned
runtime. The project runtime has no third-party dependencies; `pytest` and its
locked tools are for development and verification.

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e .
```

### macOS or Linux

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-build-isolation -e .
```

If the `repo-ready` executable is not on `PATH`, use the portable form in all
examples:

```text
python -m repo_ready ...
```

## Choose the repository to inspect

Set one variable to the repository you want to show. Quote paths containing
spaces.

### Windows PowerShell

```powershell
$Target = "C:\path\to\repository"
$Audit = Join-Path $env:TEMP "repo-ready-video"
New-Item -ItemType Directory -Path $Audit -Force | Out-Null
```

### macOS or Linux

```sh
TARGET="/path/to/repository"
AUDIT="${TMPDIR:-/tmp}/repo-ready-video"
mkdir -p "$AUDIT"
```

Do not put generated reports inside the target when recording a clean scan.
Keeping outputs in a separate audit directory makes the read-only behavior easy
to demonstrate.

## Full audit sequence for any repository

Run the following sequence. Every command is safe to run against an unfamiliar
local repository.

### 1. Readiness score

Human-readable output is best for the opening shot:

```powershell
python -m repo_ready check "$Target" --no-color
```

```sh
python -m repo_ready check "$TARGET" --no-color
```

The score is static evidence from the repository files. Failed or partial
checks include relative file findings and repair hints.

Save the machine-readable result for later comparison:

```powershell
python -m repo_ready check "$Target" --json > "$Audit\readiness.json"
```

```sh
python -m repo_ready check "$TARGET" --json > "$AUDIT/readiness.json"
```

Useful alternative formats:

```sh
python -m repo_ready check "$TARGET" --format markdown
python -m repo_ready check "$TARGET" --format sarif
```

### 2. Cross-file contract diagnosis

This is the best command for revealing contradictions hidden by a good score:

```powershell
python -m repo_ready diagnose "$Target"
python -m repo_ready diagnose "$Target" --json > "$Audit\contract-graph.json"
```

```sh
python -m repo_ready diagnose "$TARGET"
python -m repo_ready diagnose "$TARGET" --json > "$AUDIT/contract-graph.json"
```

The graph compares README commands, Makefile recipes, manifests, lockfiles,
runtime pins, CI workflows, and Docker instructions. It is static evidence,
not proof that a command will succeed when executed.

### 3. Generate the interactive Repo X-Ray report

```powershell
python -m repo_ready showcase "$Target" --output "$Audit\repo-report.html"
```

```sh
python -m repo_ready showcase "$TARGET" --output "$AUDIT/repo-report.html"
```

Open the resulting file directly in a browser:

```powershell
Start-Process "$Audit\repo-report.html"
```

```sh
open "$AUDIT/repo-report.html"       # macOS
xdg-open "$AUDIT/repo-report.html"   # Linux
```

In the report, demonstrate this order:

1. **Observed (static)** — establish the baseline score.
2. **Breakage Lab** — choose a scenario such as “Remove runtime pin” or
   “Introduce CI/runtime drift”.
3. Confirm the view is labelled **Simulated**.
4. Read the score delta and semantic baseline comparison.
5. Select a diagnostic or evidence record to highlight its related files.
6. Open **Repair notes** and show the create-only unified diff.
7. Download the receipt JSON.

The browser can open the file from disk. No local server is required.

### 4. Inspect tracked scope when Git matters

The default scope scans the filesystem. To exclude untracked local files and
scan only paths in the Git index:

```sh
python -m repo_ready tracked "$TARGET" --json
python -m repo_ready check "$TARGET" --scope tracked --json
python -m repo_ready showcase "$TARGET" --scope tracked --output "$AUDIT/tracked-report.html"
```

Tracked scope requires a readable Git index. Use filesystem scope for an
ordinary folder or an exported source tree without Git metadata.

### 5. Preview safe repairs without writing

The dry-run is the safe repair demonstration:

```sh
python -m repo_ready fix "$TARGET" --dry-run
python -m repo_ready fix "$TARGET" --dry-run --json > "$AUDIT/fix-plan.json"
python -m repo_ready fix "$TARGET" --dry-run --format diff > "$AUDIT/fix-plan.patch"
```

It only proposes missing files. Existing files, lockfiles, test commands,
machine-specific paths, and unsafe targets remain explicit manual items. The
dry run writes nothing.

Do not use `fix "$TARGET"` during a recording unless you intentionally want to
demonstrate the separate write operation. Review the dry-run output first.

### 6. Save and compare a baseline

```sh
python -m repo_ready check "$TARGET" --json > "$AUDIT/baseline.json"
python -m repo_ready compare "$TARGET" --baseline "$AUDIT/baseline.json" --json
```

The comparison ignores checkout paths and detects score changes, status changes,
and changed finding sets. Exit codes are:

| Code | Meaning |
| ---: | --- |
| `0` | No readiness regression detected, or no threshold was configured |
| `1` | Score or semantic check evidence regressed |
| `2` | Invalid path, invalid usage, or missing/invalid baseline |

## Optional artifact and rule checks

List the stable readiness rule registry:

```sh
python -m repo_ready rules --json
```

After building a wheel, inspect it without extracting or executing it:

```sh
python -m pip wheel --no-deps --no-build-isolation --wheel-dir "$AUDIT/wheel" .
python -m repo_ready artifact "$AUDIT/wheel/repo_ready-0.1.0-py3-none-any.whl" --project . --json
```

The artifact inspector checks member paths, links, duplicate members, size
limits, distribution metadata, version parity, and packaged source modules.

## Reproducible project verification

To verify this `repo-ready` checkout itself:

```sh
python -m pytest -q
```

On this Windows development host, the current result is:

```text
426 passed, 4 skipped
```

The four skips are host-capability guards for symbolic links and named pipes.
The same suite runs without those platform limitations in the clean Linux
Docker image:

```text
430 passed
```

The project also provides the single test wrapper:

```sh
make test
```

On Windows without `make`, use `python -m pytest -q` directly.

For the final submission-quality release pass, run the artifact gate after the
standard tests:

```powershell
.\.venv\Scripts\python.exe scripts\release_check.py v0.1.0
.\.venv\Scripts\python.exe scripts\release_gate.py v0.1.0
```

The gate verifies the wheel, source distribution, package metadata, showcase,
browser flow, and clean Docker suite. Use `--skip-docker --skip-browser` only
when Docker or Chromium is intentionally unavailable.

For clean-room verification with Docker:

```sh
docker build -t repo-ready .
docker run --rm --network none repo-ready python -m pytest -q
```

To scan another repository in the built image without allowing writes:

```sh
docker run --rm --network none \
  -v "/path/to/another-repository":/work:ro \
  repo-ready repo-ready check /work --json
```

## Recommended 90-second recording script

Use a repository with visible contradictions or missing setup files. The
checked-in deterministic demo fixture is available at
`examples/showcase-demo`. It enables all four Breakage Lab scenarios.

### Shot 1 — establish the score

Run `check` and say:

> “This is a static readiness score. It tells us what the repository claims,
> not whether we have executed the project.”

### Shot 2 — reveal the hidden contract

Run `diagnose` and point to a mismatch or missing reference:

> “The score is only one view. Repo-ready also follows the contract between
> the README, runtime pin, CI, Dockerfile, and dependency files.”

### Shot 3 — open Repo X-Ray

Open `repo-report.html` and show the baseline observation, evidence ledger, and
diagnostic list.

### Shot 4 — simulate breakage

Select a Breakage Lab scenario:

> “This is labelled simulated. The tool changed an in-memory view, not the
> repository on disk.”

Show the score delta, affected file, and new diagnostic.

### Shot 5 — preview recovery

Show Repair notes and the create-only patch:

> “This is a reviewable projection, not a claim that the build was run.”

### Shot 6 — preserve the evidence

Download the receipt and point out the fingerprint and limitations:

> “The receipt identifies this static evidence snapshot. It is not a build
> certificate or a security guarantee.”

## Safety claims to make accurately

- `repo-ready` does not execute target code.
- It does not invoke target package managers or test commands.
- Breakage Lab scenarios use immutable in-memory repository overlays.
- Repair Preview uses create-only projected changes and keeps existing files
  untouched.
- The HTML report is self-contained and offline.
- The score, graph, scenarios, and receipt describe static evidence; they do
  not prove a runtime build or test result.

## Troubleshooting during recording

### “repository path is not a directory or is unreadable”

Check the path and quote it if it contains spaces. On Windows PowerShell,
prefer an absolute path in `$Target`.

### `tracked` reports an unavailable Git index

Use filesystem scope, or run the command from a real Git checkout with a
readable `.git/index`.

### The report opens but appears static

The static fallback is intentional. Check that the HTML file was generated
completely and that the browser has JavaScript enabled. The report remains
readable without JavaScript.

### A scenario is unavailable

That is an expected state, not a failure. The report explains which contract
was not observed. Use a repository with a runtime pin, test entry point, or
explicit dependency-file reference to demonstrate that scenario.
