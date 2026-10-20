# Changelog

All notable changes to `repo-ready` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and releases use [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Release validation and artifact verification workflow.
- Static ecosystem provider support for Go, Rust, .NET, Java/Kotlin, and Ruby.

## [0.2.0] - 2026-10-20

### Added

- Opt-in `scripts/verify.py` execution with detected or explicit test commands.
- Bounded verification timeouts and captured output in text or JSON form.
- RR09 test-command discovery reused by the verification boundary.
- Automatic scan scope selection, progress reporting, inventory metadata, and
  repository-integrity observations.
- Context-aware scoring profiles and improved nested component, environment,
  workflow, fixer, scenario, and report diagnostics.

## [0.1.0] - 2026-10-20

### Added

- Offline readiness scoring for local repositories.
- Cross-file contract diagnostics, baseline comparison, tracked-tree scope, and safe repair previews.
- Self-contained Repo X-Ray showcase reports with simulated Breakage Lab scenarios.
- Wheel and source-distribution inspection without extraction or execution.

[Unreleased]: https://github.com/m4milaad/repo-ready/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/m4milaad/repo-ready/releases/tag/v0.2.0
[0.1.0]: https://github.com/m4milaad/repo-ready/releases/tag/v0.1.0
