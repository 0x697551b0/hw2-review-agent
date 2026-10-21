"""归一化：把 codeguard 的输出翻译成工作流契约。

不直接透传它的数据结构，一是 codeguard 是独立产品，ReviewReport 会随它自己演进，
工作流不该被上游字段改名牵着走；二是下游其实只要"文件 + 行号 + 规则 + 级别 +
是否可修 + 稳定 ID"这几样。

稳定 ID 用「规则 + 文件 + 归一化标题」做哈希，行号只当展示信息用。第 1 轮修完、
第 2 轮复审，行号会整体位移，用行号构造的 ID 永远对不上"这条到底修好没有"。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from review_agent.fixability import classify
from review_agent.protocol import (
    SEVERITY_ORDER,
    FindingContract,
    count_blocking,
    count_by_severity,
    is_blocking,
)

#: 打分权重：一条问题扣多少分（与"阻断级别"的权重明显区分开）
_SCORE_PENALTY: dict[str, int] = {
    "critical": 25,
    "high": 10,
    "medium": 4,
    "low": 1,
    "info": 0,
}

#: 归一化标题时保留的字符
_TITLE_NOISE = re.compile(r"[\s:：,，.。()（）\[\]【】'\"`]")


def stable_finding_id(rule_id: str, file: str, title: str, *, occurrence: int = 1) -> str:
    """
    生成跨轮次稳定的发现 ID。

    occurrence 区分同文件、同规则、同标题的重复命中（比如一个文件里有 3 处未使用
    导入），它们哈希一样，靠序号分开。
    """
    basis = f"{rule_id}|{_normalize_file(file)}|{_normalize_title(title)}"
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:8].upper()
    suffix = "" if occurrence <= 1 else f"-{occurrence}"
    return f"F{digest}{suffix}"


def normalize_findings(
    raw_findings: Iterable[Any],
    *,
    root: str | Path,
    default_source: str = "static",
) -> list[FindingContract]:
    """
    批量转成 FindingContract。

    入参接受 dataclass 实例也接受 dict，将来 codeguard 改成先序列化成 JSON 再交
    过来，这里也不用动。
    """
    base = Path(root).resolve()
    contracts: list[FindingContract] = []
    seen: dict[str, int] = {}

    for raw in raw_findings:
        data = _as_mapping(raw)
        if not data:
            continue
        rule_id = str(data.get("rule_id") or "")
        file = _relative(data.get("file") or "", base)
        title = str(data.get("title") or "未命名问题")
        severity = str(data.get("severity") or "medium").strip().lower()
        if severity not in SEVERITY_ORDER:
            severity = "medium"

        basis = f"{rule_id}|{file}|{_normalize_title(title)}"
        seen[basis] = seen.get(basis, 0) + 1
        fix = classify(rule_id)

        contracts.append(
            FindingContract(
                finding_id=stable_finding_id(rule_id, file, title, occurrence=seen[basis]),
                rule_id=rule_id,
                severity=severity,
                category=str(data.get("category") or "general"),
                file=file,
                line=data.get("line"),
                title=title,
                evidence=str(data.get("evidence") or ""),
                explanation=str(data.get("explanation") or ""),
                suggestion=str(data.get("suggestion") or ""),
                source=str(data.get("source") or default_source),
                fixable=fix.fixable,
                confidence=_as_float(data.get("confidence")),
                status="open",
                note=fix.reason if not fix.fixable else "",
            )
        )

    contracts.sort(key=lambda f: (SEVERITY_ORDER.index(f.severity), f.file, f.line or 0))
    return contracts


def summarize(
    findings: Sequence[FindingContract],
    *,
    blocking: Sequence[str] = ("critical", "high"),
) -> dict[str, Any]:
    """计算质量门禁所需的全部派生指标。"""
    counts = count_by_severity(findings)
    blocking_count = count_blocking(findings, blocking)
    return {
        "counts": counts,
        "total": len(findings),
        "blocking": blocking_count,
        "blocking_severities": list(blocking),
        "passed": blocking_count == 0,
        "fixable": sum(1 for f in findings if f.fixable),
        "needs_human": sum(1 for f in findings if not f.fixable),
        "score": score(findings),
        "overall_risk": overall_risk(counts),
    }


def score(findings: Sequence[FindingContract]) -> int:
    """0~100 的健康分。只用于趋势对比，不能当合格证明。"""
    penalty = sum(_SCORE_PENALTY.get(f.severity, 0) for f in findings)
    return max(0, min(100, 100 - penalty))


def overall_risk(counts: dict[str, int]) -> str:
    for severity in SEVERITY_ORDER:
        if severity == "info":
            break
        if counts.get(severity):
            return severity
    return "clean"


def gate_detail(
    findings: Sequence[FindingContract], *, blocking: Sequence[str] = ("critical", "high")
) -> list[dict[str, Any]]:
    """门禁判定明细：逐条说明为什么通过/不通过。"""
    rows = []
    for finding in findings:
        rows.append(
            {
                "finding_id": finding.finding_id,
                "severity": finding.severity,
                "rule_id": finding.rule_id,
                "location": finding.location,
                "blocks": is_blocking(finding.severity, blocking),
                "fixable": finding.fixable,
            }
        )
    return rows


def resolve_targets(
    root: str | Path,
    *,
    patterns: Sequence[str] = ("*.py",),
    exclude_dirs: Sequence[str] = (
        ".git", "__pycache__", ".venv", "venv", "node_modules",
        ".mypy_cache", ".pytest_cache", "build", "dist", ".idea",
    ),
) -> list[Path]:
    """
    枚举待审查文件。

    显式传 patterns，是因为目标目录里常常还躺着 draft_manifest.json、报告这些
    非源码产物，扫进去全是噪声。
    """
    base = Path(root).resolve()
    if not base.exists():
        raise FileNotFoundError(f"审查目标不存在：{base}")
    if base.is_file():
        return [base]

    excluded = set(exclude_dirs)
    files: list[Path] = []
    for pattern in patterns:
        for path in sorted(base.rglob(pattern)):
            if not path.is_file():
                continue
            if any(part in excluded for part in path.parts):
                continue
            if path not in files:
                files.append(path)
    return files


# 内部
def _as_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    to_dict = getattr(raw, "to_dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
            return dict(data) if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - 上游对象异常不应打断整批归一化
            return {}
    # 退化路径：按属性名抓取（dataclass 场景）
    data = {}
    for key in ("rule_id", "severity", "category", "file", "line", "title", "evidence",
                "explanation", "suggestion", "source", "confidence"):
        if hasattr(raw, key):
            data[key] = getattr(raw, key)
    return data


def _relative(raw: str | Path, base: Path) -> str:
    """统一成相对审查根目录的 posix 路径；越界则原样返回。"""
    if not raw:
        return ""
    path = Path(str(raw))
    try:
        resolved = path.resolve() if path.is_absolute() else (base / path).resolve()
        return resolved.relative_to(base).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


def _normalize_file(raw: str) -> str:
    return str(raw).replace("\\", "/").lower()


def _normalize_title(title: str) -> str:
    return _TITLE_NOISE.sub("", str(title)).lower()[:40]


def _as_float(raw: Any) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
