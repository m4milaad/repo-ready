"""Static ecosystem profiles used by detection and readiness checks.

Profiles describe conventions only.  They never execute a compiler, package
manager, test runner, or project code.
"""

from __future__ import annotations

import fnmatch
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath

from .context import RepoContext


@dataclass(frozen=True)
class EcosystemProfile:
    name: str
    marker_files: tuple[str, ...]
    lock_files: tuple[str, ...]
    runtime_files: tuple[str, ...]
    test_command: str
    source_suffixes: tuple[str, ...]
    ignore_names: tuple[str, ...]
    container_prefixes: tuple[str, ...]

    def matches(self, context: RepoContext) -> bool:
        return any(
            any(entry.path == marker or fnmatch.fnmatchcase(entry.path, marker) for entry in context)
            for marker in self.marker_files
        )


ECOSYSTEM_PROFILES: tuple[EcosystemProfile, ...] = (
    EcosystemProfile(
        "python",
        ("pyproject.toml", "requirements*.txt", "setup.py", "setup.cfg", "Pipfile"),
        ("uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock", "requirements*.lock"),
        (".python-version", "runtime.txt", ".tool-versions"),
        "python -m pytest -q",
        (".py", ".pyi"),
        ("__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache"),
        ("python:",),
    ),
    EcosystemProfile(
        "node",
        ("package.json",),
        ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"),
        (".nvmrc", ".node-version", ".tool-versions"),
        "npm test",
        (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
        ("node_modules", ".yarn", ".pnpm-store"),
        ("node:",),
    ),
    EcosystemProfile(
        "go",
        ("go.mod",),
        ("go.sum",),
        (".go-version", "go.mod"),
        "go test ./...",
        (".go",),
        ("vendor",),
        ("golang:",),
    ),
    EcosystemProfile(
        "rust",
        ("Cargo.toml",),
        ("Cargo.lock",),
        ("rust-toolchain", "rust-toolchain.toml", "Cargo.toml"),
        "cargo test",
        (".rs",),
        ("target",),
        ("rust:",),
    ),
    EcosystemProfile(
        "dotnet",
        ("*.sln", "*.slnx", "*.csproj", "global.json"),
        ("packages.lock.json",),
        ("global.json", ".dotnet-version"),
        "dotnet test",
        (".cs", ".fs", ".vb"),
        ("bin", "obj"),
        ("mcr.microsoft.com/dotnet/",),
    ),
    EcosystemProfile(
        "java",
        ("pom.xml", "build.gradle", "build.gradle.kts", "gradlew"),
        ("gradle.lockfile",),
        (".java-version", ".sdkmanrc", "pom.xml", "build.gradle", "build.gradle.kts"),
        "mvn test",
        (".java", ".kt", ".kts"),
        (".gradle", "build"),
        ("maven:", "gradle:", "eclipse-temurin:", "openjdk:"),
    ),
    EcosystemProfile(
        "ruby",
        ("Gemfile",),
        ("Gemfile.lock",),
        (".ruby-version", ".ruby-gemset"),
        "bundle exec rake test",
        (".rb", ".rake"),
        (".bundle",),
        ("ruby:",),
    ),
)

_PROFILES = {profile.name: profile for profile in ECOSYSTEM_PROFILES}


def profile_for(name: str) -> EcosystemProfile | None:
    return _PROFILES.get(name)


def detected_profiles(context: RepoContext) -> tuple[EcosystemProfile, ...]:
    return tuple(profile for profile in ECOSYSTEM_PROFILES if profile.matches(context))


def _read_json(context: RepoContext, path: str) -> dict[str, object] | None:
    text = context.read_text(path)
    if text is None:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _read_toml(context: RepoContext, path: str) -> dict[str, object] | None:
    text = context.read_text(path)
    if text is None:
        return None
    try:
        value = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _dependency_name(value: str) -> str | None:
    match = re.match(r"\s*([A-Za-z0-9_.@/-]+)", value)
    return match.group(1).casefold() if match else None


def declared_dependencies(context: RepoContext, ecosystem: str) -> tuple[bool, set[str]]:
    """Return ``(known, names)`` for non-Python/Node dependency manifests."""

    if ecosystem == "go":
        text = context.read_text("go.mod")
        if text is None:
            return False, set()
        names = {
            match.group(1).casefold()
            for line in text.splitlines()
            if (match := re.match(r"\s*require\s+([^\s]+)\s+v?\S+", line))
        }
        names.update(
            match.group(1).casefold()
            for line in text.splitlines()
            if (match := re.match(r"\s*([^\s()]+)\s+v?\S+\s*(?://.*)?$", line))
            and not line.strip().startswith(("module ", "go ", "toolchain ", "require ", "replace ", "exclude "))
        )
        return True, names
    if ecosystem == "rust":
        document = _read_toml(context, "Cargo.toml")
        if document is None:
            return False, set()
        names: set[str] = set()
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            values = document.get(section)
            if isinstance(values, dict):
                names.update(str(name).casefold() for name in values)
        return True, names
    if ecosystem == "dotnet":
        names: set[str] = set()
        found = False
        for entry in context:
            if not entry.path.endswith((".csproj", ".fsproj", ".vbproj")):
                continue
            text = context.read_text(entry)
            if text is None:
                return False, set()
            found = True
            names.update(match.group(1).casefold() for match in re.finditer(r"PackageReference\s+Include\s*=\s*[\"']([^\"']+)", text, re.I))
        return (True, names) if found else (True, set())
    if ecosystem == "java":
        names: set[str] = set()
        known = False
        pom = context.read_text("pom.xml")
        if pom is not None:
            known = True
            groups = re.findall(r"<groupId>\s*([^<]+)\s*</groupId>\s*<artifactId>\s*([^<]+)", pom, re.I)
            names.update(f"{group}:{artifact}".casefold() for group, artifact in groups)
        for path in ("build.gradle", "build.gradle.kts"):
            text = context.read_text(path)
            if text is None:
                continue
            known = True
            for match in re.finditer(r"(?:implementation|api|testImplementation|runtimeOnly)\s*[\( ]\s*[\"']([^\"']+)", text):
                coordinate = match.group(1).split(":")
                names.add(":".join(coordinate[:2]).casefold() if len(coordinate) >= 2 else match.group(1).casefold())
        return known, names
    if ecosystem == "ruby":
        text = context.read_text("Gemfile")
        if text is None:
            return False, set()
        return True, {match.group(1).casefold() for match in re.finditer(r"^\s*gem\s*[\( ]\s*[\"']([^\"']+)", text, re.MULTILINE)}
    return False, set()


def runtime_values(context: RepoContext, ecosystem: str) -> tuple[tuple[str, str, int], ...]:
    """Return static runtime declarations as ``(file, value, line)``."""

    values: list[tuple[str, str, int]] = []
    if ecosystem == "go":
        text = context.read_text(".go-version")
        if text and text.strip():
            values.append((".go-version", text.strip(), 1))
        text = context.read_text("go.mod")
        if text:
            for line, value in enumerate(text.splitlines(), 1):
                match = re.match(r"\s*go\s+(\d+(?:\.\d+){1,2})", value)
                if match:
                    values.append(("go.mod", match.group(1), line))
    elif ecosystem == "rust":
        for path in ("rust-toolchain", "rust-toolchain.toml"):
            text = context.read_text(path)
            if text and text.strip():
                values.append((path, text.strip().splitlines()[0].strip(), 1))
        text = context.read_text("Cargo.toml")
        if text:
            for line, value in enumerate(text.splitlines(), 1):
                match = re.match(r"\s*rust-version\s*=\s*[\"']([^\"']+)", value)
                if match:
                    values.append(("Cargo.toml", match.group(1), line))
    elif ecosystem == "dotnet":
        for path in (".dotnet-version", "global.json"):
            text = context.read_text(path)
            if not text:
                continue
            if path == "global.json":
                value = _read_json(context, path) or {}
                sdk = value.get("sdk") if isinstance(value, dict) else None
                version = sdk.get("version") if isinstance(sdk, dict) else None
                if isinstance(version, str):
                    values.append((path, version, 1))
            else:
                values.append((path, text.strip(), 1))
    elif ecosystem == "java":
        for path in (".java-version", ".sdkmanrc"):
            text = context.read_text(path)
            if text and text.strip():
                values.append((path, text.strip().splitlines()[0].strip(), 1))
        for path in ("pom.xml", "build.gradle", "build.gradle.kts"):
            text = context.read_text(path)
            if text:
                for line, value in enumerate(text.splitlines(), 1):
                    match = re.search(r"(?:<maven.compiler.release>|sourceCompatibility\s*[=:])\s*[\"']?([0-9]+(?:\.[0-9]+)*)", value)
                    if match:
                        values.append((path, match.group(1), line))
    elif ecosystem == "ruby":
        for path in (".ruby-version", ".ruby-gemset"):
            text = context.read_text(path)
            if text and text.strip():
                values.append((path, text.strip().splitlines()[0].strip(), 1))
    return tuple(values)


def source_suffixes(ecosystems: tuple[str, ...]) -> set[str]:
    return {suffix for profile in detected_profiles_from_names(ecosystems) for suffix in profile.source_suffixes}


def detected_profiles_from_names(names: tuple[str, ...] | list[str] | set[str]) -> tuple[EcosystemProfile, ...]:
    return tuple(profile for profile in ECOSYSTEM_PROFILES if profile.name in names)


def conventional_test_command(context: RepoContext, ecosystem: str) -> str | None:
    profile = profile_for(ecosystem)
    if profile is None or not profile.matches(context):
        return None
    if ecosystem == "java":
        if context.has_file("gradlew"):
            return "./gradlew test"
        if context.has_file("pom.xml"):
            return "mvn test"
        if context.has_file("build.gradle") or context.has_file("build.gradle.kts"):
            return "gradle test"
    if ecosystem == "ruby" and (context.has_file("Rakefile") or context.has_file(".rspec")):
        return profile.test_command
    return profile.test_command


__all__ = [
    "ECOSYSTEM_PROFILES",
    "EcosystemProfile",
    "conventional_test_command",
    "declared_dependencies",
    "detected_profiles",
    "detected_profiles_from_names",
    "profile_for",
    "runtime_values",
    "source_suffixes",
]
