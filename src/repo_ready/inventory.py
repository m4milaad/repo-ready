"""Deterministic scan inventory metadata for CLI and showcase reports."""

from __future__ import annotations

from pathlib import Path

from .context import RepoContext
from .tracked import TrackedReport


def _git_revision(root: Path) -> str | None:
    """Read a lightweight Git revision without invoking Git."""

    marker = root / ".git"
    try:
        if marker.is_file():
            value = marker.read_text(encoding="utf-8").strip()
            if value.startswith("gitdir:"):
                marker = (root / value[7:].strip()).resolve(strict=False)
        head = marker / "HEAD"
        value = head.read_text(encoding="utf-8").strip()
        if value.startswith("ref:"):
            ref = value[5:].strip()
            ref_path = marker / ref
            if ref_path.is_file():
                return ref_path.read_text(encoding="ascii").strip() or None
            packed = marker / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="ascii").splitlines():
                    if line and not line.startswith("#"):
                        revision, _, candidate = line.partition(" ")
                        if candidate == ref:
                            return revision or None
            return None
        return value or None
    except (OSError, UnicodeError, RuntimeError):
        return None


def scan_inventory(
    context: RepoContext,
    *,
    requested_scope: str,
    effective_scope: str,
    tracked: TrackedReport | None = None,
) -> dict[str, object]:
    """Build stable, non-sensitive metadata describing one indexed scan."""

    return {
        "requested_scope": requested_scope,
        "effective_scope": effective_scope,
        "files": context.observed_files,
        "bytes": context.observed_bytes,
        "excluded": getattr(context, "excluded_files", 0),
        "truncated": context.truncated,
        "truncation_reasons": list(context.truncation_reasons),
        "inventory_fingerprint": context.inventory_fingerprint,
        "git_revision": _git_revision(context.root),
        "tracked_files": len(tracked.files) if tracked is not None else None,
        "tracked_index_version": tracked.index_version if tracked is not None else None,
    }


__all__ = ["scan_inventory"]
