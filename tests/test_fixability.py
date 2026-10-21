"""可修复性表（fixability.py）的测试。

这张表是审查 Agent 与修复 Agent 之间的**共享词汇表**。若两边不一致，就会出现
"审查说能自动修、修复却没有策略"的静默失败。因此这里不只测本仓库的行为，还测
"这张表本身是自洽的"。
"""

from __future__ import annotations

from review_agent.fixability import (
    FIXABLE_STRATEGIES,
    NOT_AUTOFIXABLE,
    classify,
    is_fixable,
    strategy_of,
    table,
)


def test_known_fixable_rules() -> None:
    for rule in ("CG105", "CG106", "CG202", "CG203", "CG204", "CG206", "CG207", "CG402", "CG403"):
        verdict = classify(rule)
        assert verdict.fixable, rule
        assert verdict.strategy, rule


def test_known_unfixable_rules_have_reasons() -> None:
    for rule in ("CG101", "CG301", "CG304", "CG307", "CG401"):
        verdict = classify(rule)
        assert not verdict.fixable, rule
        assert verdict.reason, f"{rule} 必须说明为什么不能自动修"


def test_unknown_rule_defaults_to_unfixable() -> None:
    """未知规则宁可保守：判为不可修，交给人工。"""
    verdict = classify("CG999")
    assert not verdict.fixable
    assert "未知" in verdict.reason


def test_case_and_space_insensitive() -> None:
    assert is_fixable("cg106")
    assert is_fixable(" CG106 ")
    assert strategy_of("cg106") == "parameterize_sql"


def test_tables_are_disjoint() -> None:
    overlap = set(FIXABLE_STRATEGIES) & set(NOT_AUTOFIXABLE)
    assert not overlap, f"同一规则不能既可修又不可修：{overlap}"


def test_every_row_is_documented() -> None:
    rows = table()
    assert len(rows) == len(FIXABLE_STRATEGIES) + len(NOT_AUTOFIXABLE)
    for row in rows:
        assert row["rule_id"]
        if row["fixable"]:
            assert row["strategy"], row
        else:
            assert row["reason"], row


def test_strategy_names_are_snake_case() -> None:
    for strategy in FIXABLE_STRATEGIES.values():
        assert strategy == strategy.lower()
        assert " " not in strategy
