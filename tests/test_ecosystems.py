from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_ready.contracts import build_contract_graph
from repo_ready.context import RepoContext
from repo_ready.detector import detect_ecosystems
from repo_ready.scoring import report


COMMON = {
    "README.md": "# Demo\n\n## Setup\nInstall dependencies.\n\n## Run\nRun the app.\n\n## Test\nRun the test command.\n",
    "LICENSE": "MIT License\n" + "Permission granted.\n" * 8,
    "CONTRIBUTING.md": "# Contributing\n" + "Use the documented workflow and run the tests before submitting changes.\n" * 3,
    ".github/ISSUE_TEMPLATE/bug_report.md": "# Bug report\nDescribe the problem.\n",
    ".github/PULL_REQUEST_TEMPLATE.md": "# Pull request\nExplain the change.\n",
    ".env.example": "APP_MODE=demo\n",
    ".gitignore": ".env\n",
    ".github/workflows/ci.yml": "name: CI\nsteps:\n  - run: test\n",
}


PROJECTS: dict[str, dict[str, str]] = {
    "go": {
        "go.mod": "module example.com/demo\n\ngo 1.22\n\nrequire github.com/acme/lib v1.2.3\n",
        "go.sum": "github.com/acme/lib v1.2.3 h1:abc\ngithub.com/acme/lib v1.2.3/go.mod h1:def\n",
        ".go-version": "1.22.0\n",
        "Dockerfile": "FROM golang:1.22\nCMD [\"go\", \"test\", \"./...\"]\n",
        ".gitignore": ".env\nvendor\n",
        "main.go": "package main\n",
    },
    "rust": {
        "Cargo.toml": "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n\n[dependencies]\nserde = \"1\"\n",
        "Cargo.lock": "version = 3\n\n[[package]]\nname = \"demo\"\nversion = \"0.1.0\"\n\n[[package]]\nname = \"serde\"\nversion = \"1.0.0\"\n",
        "rust-toolchain": "1.80.0\n",
        "Dockerfile": "FROM rust:1.80\nCMD [\"cargo\", \"test\"]\n",
        ".gitignore": ".env\ntarget\n",
        "src/main.rs": "fn main() {}\n",
    },
    "dotnet": {
        "demo.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\"><ItemGroup><PackageReference Include=\"Newtonsoft.Json\" Version=\"13.0.3\" /></ItemGroup></Project>\n",
        "packages.lock.json": json.dumps({"version": 1, "dependencies": {"net8.0": {"Newtonsoft.Json": {"type": "Direct", "resolved": "13.0.3"}}}}),
        "global.json": json.dumps({"sdk": {"version": "8.0.100"}}),
        "Dockerfile": "FROM mcr.microsoft.com/dotnet/sdk:8.0\nCMD [\"dotnet\", \"test\"]\n",
        ".gitignore": ".env\nbin\nobj\n",
    },
    "java": {
        "build.gradle": "plugins { id 'java' }\ndependencies { implementation(\"org.example:lib:1.0\") }\n",
        "gradle.lockfile": "org.example:lib:1.0=compileClasspath\n",
        ".java-version": "21\n",
        "Dockerfile": "FROM eclipse-temurin:21\nCMD [\"./gradlew\", \"test\"]\n",
        ".gitignore": ".env\n.gradle\nbuild\n",
        "gradlew": "#!/bin/sh\n",
    },
    "ruby": {
        "Gemfile": "source 'https://rubygems.org'\ngem \"rack\", \"~> 3.0\"\n",
        "Gemfile.lock": "GEM\n  specs:\n    rack (3.0.0)\n\nDEPENDENCIES\n  rack (~> 3.0)\n",
        ".ruby-version": "3.3.0\n",
        "Dockerfile": "FROM ruby:3.3\nCMD [\"bundle\", \"exec\", \"rake\", \"test\"]\n",
        ".gitignore": ".env\n.bundle\n",
        "Rakefile": "task :test\n",
    },
}


@pytest.mark.parametrize("ecosystem", tuple(PROJECTS))
def test_supported_ecosystem_profile_scores_static_contracts(tmp_path: Path, ecosystem: str) -> None:
    files = dict(COMMON)
    files.update(PROJECTS[ecosystem])
    if ecosystem == "go":
        files[".github/workflows/ci.yml"] = "name: CI\nsteps:\n  - uses: actions/setup-go@v5\n    with:\n      go-version: '1.22'\n  - run: go test ./...\n"
    elif ecosystem == "rust":
        files[".github/workflows/ci.yml"] = "name: CI\nsteps:\n  - uses: actions/setup-rust@v1\n    with:\n      rust-version: '1.80'\n  - run: cargo test\n"
    elif ecosystem == "dotnet":
        files[".github/workflows/ci.yml"] = "name: CI\nsteps:\n  - uses: actions/setup-dotnet@v4\n    with:\n      dotnet-version: '8.0'\n  - run: dotnet test\n"
    elif ecosystem == "java":
        files[".github/workflows/ci.yml"] = "name: CI\nsteps:\n  - uses: actions/setup-java@v4\n    with:\n      java-version: '21'\n  - run: ./gradlew test\n"
    elif ecosystem == "ruby":
        files[".github/workflows/ci.yml"] = "name: CI\nsteps:\n  - uses: ruby/setup-ruby@v1\n    with:\n      ruby-version: '3.3'\n  - run: bundle exec rake test\n"
    repository = tmp_path / ecosystem
    for relative, content in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert detect_ecosystems(repository) == [ecosystem]
    value = report(repository)
    checks = {item["id"]: item for item in value["checks"]}
    assert checks["RR06"]["status"] == "pass"
    assert checks["RR07"]["status"] == "pass"
    assert checks["RR09"]["status"] == "pass"
    assert checks["RR12"]["status"] == "pass"

    graph = build_contract_graph(RepoContext(repository))
    assert any(item.attributes and dict(item.attributes).get("ecosystem") == ecosystem for item in graph.evidence)
    assert any(item.normalized.endswith("test") or item.subject.endswith("-test") for item in graph.evidence)
