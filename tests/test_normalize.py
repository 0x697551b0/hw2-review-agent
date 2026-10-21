"""归一化（normalize.py）的测试。

盯两件事：行号变了 ID 不能变，不然跨轮次没法追"这条修好没有"；passed、blocking、
counts、score 这些门禁指标得算对、说得清。
"""

from __future__ import annotations

from pathlib import Path

from review_agent.normalize import (
    gate_detail,
    normalize_findings,
    resolve_targets,
    score,
    stable_finding_id,
    summarize,
)
from review_agent.protocol import FindingContract, is_blocking

from tests.support import BUGGY_SOURCE, expect_raises, tempdir, write


def _fake_raw(line: int = 10, severity: str = "high", rule: str = "CG106", file: str = "a.py") -> dict:
    return {
        "title": "SQL 语句由拼接构造",
        "severity": severity,
        "category": "security",
        "rule_id": rule,
        "file": file,
        "line": line,
        "evidence": "cursor.execute(f'...')",
        "explanation": "拼接导致注入",
        "suggestion": "参数化查询",
    }


def test_normalize_maps_fields() -> None:
    with tempdir() as tmp:
        findings = normalize_findings([_fake_raw()], root=tmp)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "CG106"
    assert finding.severity == "high"
    assert finding.file == "a.py"
    assert isinstance(finding, FindingContract)


def test_finding_id_stable_across_line_change() -> None:
    """行号变不影响 ID，这是跨轮次闭环的前提。"""
    first = stable_finding_id("CG106", "a.py", "SQL 语句由拼接构造")
    second = stable_finding_id("CG106", "a.py", "SQL 语句由拼接构造")
    assert first == second
    third = stable_finding_id("CG106", "a.py", "完全不同的标题")
    assert first != third


def test_duplicate_findings_get_distinct_ids() -> None:
    with tempdir() as tmp:
        raw = [_fake_raw(line=1), _fake_raw(line=2)]
        findings = normalize_findings(raw, root=tmp)
    ids = {f.finding_id for f in findings}
    assert len(ids) == 2, ids


def test_paths_are_relativized() -> None:
    with tempdir() as tmp:
        absolute = str((tmp / "a.py").resolve())
        findings = normalize_findings([_fake_raw(file=absolute)], root=tmp)
    assert findings[0].file == "a.py", findings[0].file


def test_findings_sorted_by_severity() -> None:
    with tempdir() as tmp:
        raw = [
            _fake_raw(1, "low", "CG304"),
            _fake_raw(2, "critical", "CG105"),
            _fake_raw(3, "medium", "CG202"),
        ]
        findings = normalize_findings(raw, root=tmp)
    assert [f.severity for f in findings] == ["critical", "medium", "low"]


def test_fixable_flag_comes_from_fixability_table() -> None:
    with tempdir() as tmp:
        raw = [_fake_raw(1, "high", "CG105"), _fake_raw(2, "low", "CG304")]
        findings = normalize_findings(raw, root=tmp)
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["CG105"].fixable, "硬编码密钥应可自动修"
    assert not by_rule["CG304"].fixable, "文档字符串不应自动修"
    assert by_rule["CG304"].note, "不可修的要给出理由"


def test_summarize_gate() -> None:
    findings = [
        FindingContract(finding_id="F1", severity="high", rule_id="CG106", fixable=True),
        FindingContract(finding_id="F2", severity="low", rule_id="CG304", fixable=False),
    ]
    summary = summarize(findings)
    assert summary["passed"] is False
    assert summary["blocking"] == 1
    assert summary["fixable"] == 1
    assert summary["needs_human"] == 1
    assert summary["counts"]["high"] == 1


def test_summarize_passes_when_no_blocking() -> None:
    findings = [FindingContract(finding_id="F1", severity="medium")]
    summary = summarize(findings)
    assert summary["passed"] is True
    assert summary["overall_risk"] == "medium"


def test_blocking_level_is_configurable() -> None:
    findings = [FindingContract(finding_id="F1", severity="medium", fixable=True)]
    assert summarize(findings)["passed"] is True
    strict = summarize(findings, blocking=("critical", "high", "medium"))
    assert strict["passed"] is False


def test_score_decreases_with_severity() -> None:
    light = [FindingContract(finding_id="F1", severity="info")]
    heavy = [FindingContract(finding_id="F2", severity="critical")]
    assert score(light) > score(heavy)
    assert 0 <= score(heavy) <= 100


def test_gate_detail_explains_each_item() -> None:
    findings = [FindingContract(finding_id="F1", severity="high", rule_id="CG106", file="a.py", line=3)]
    rows = gate_detail(findings)
    assert rows[0]["blocks"] is True
    assert rows[0]["location"] == "a.py:3"
    assert rows[0]["finding_id"] == "F1"


def test_resolve_targets_filters_and_excludes() -> None:
    with tempdir() as tmp:
        write(tmp / "a.py", "x = 1\n")
        write(tmp / "b.txt", "not code\n")
        write(tmp / "__pycache__" / "c.py", "x = 1\n")
        found = resolve_targets(tmp)
    names = sorted(path.name for path in found)
    assert names == ["a.py"], names


def test_resolve_targets_missing_raises() -> None:
    expect_raises(FileNotFoundError, resolve_targets, "/definitely/not/here")


def test_normalize_accepts_objects_with_to_dict() -> None:
    class Fake:
        def to_dict(self) -> dict:
            return _fake_raw()

    with tempdir() as tmp:
        findings = normalize_findings([Fake()], root=tmp)
    assert findings[0].rule_id == "CG106"


def test_is_blocking_helper() -> None:
    assert is_blocking("critical")
    assert not is_blocking("low")
    assert is_blocking("low", ("low",))
