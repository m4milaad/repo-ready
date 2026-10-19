from __future__ import annotations

from repo_ready.registry import rule_descriptors


def test_rule_registry_matches_the_scoring_contract() -> None:
    rules = rule_descriptors()

    assert [rule.id for rule in rules] == [f"RR{index:02d}" for index in range(1, 14)]
    assert sum(rule.weight for rule in rules) == 100
    assert all(rule.title and isinstance(rule.fixable, bool) for rule in rules)
