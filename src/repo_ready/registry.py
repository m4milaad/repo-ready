"""Public metadata registry for readiness rules.

Rule execution remains in :mod:`repo_ready.checks`; this module provides a
stable descriptor layer for integrations, profiles, and future check modules
without making callers import private implementation functions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .checks import CHECK_FIXABLE, CHECK_HINTS, CHECK_TITLES, CHECK_WEIGHTS, RULES


@dataclass(frozen=True)
class RuleDescriptor:
    id: str
    title: str
    weight: int
    fixable: bool
    fix_hint: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "weight": self.weight,
            "fixable": self.fixable,
            "fix_hint": self.fix_hint,
        }


def rule_descriptors() -> tuple[RuleDescriptor, ...]:
    """Return rule metadata in the same stable order as readiness reports."""

    return tuple(
        RuleDescriptor(
            id=rule.id,
            title=CHECK_TITLES[rule.id],
            weight=CHECK_WEIGHTS[rule.id],
            fixable=CHECK_FIXABLE[rule.id],
            fix_hint=CHECK_HINTS[rule.id],
        )
        for rule in RULES
    )


__all__ = ["RuleDescriptor", "rule_descriptors"]
