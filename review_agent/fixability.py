"""可修 / 不可修：哪些规则命中能放心自动修，哪些必须转人工。

这张表 review-agent 和 fix-agent 共用（docs/CONTRACT.md 里有同一份），放在审查侧
是因为审查最清楚"这条是哪个规则命中的"，报告也能一开始就说清"15 条里 7 条可自动修"，
不用等修复 Agent 试完。

判可修要同时满足：改法是局部的（不动签名和对外契约）、结果确定（不靠业务判断）、
改完能验证（重扫一次问题消失且没引入新的阻断级）。不满足的一律转人工：让 Agent
假装能修，比明说"这需要人"糟得多。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 可自动修复的规则 → 修复策略名（与 fix_agent 的策略注册表一一对应）
FIXABLE_STRATEGIES: dict[str, str] = {
    "CG105": "move_secret_to_env",
    "CG106": "parameterize_sql",
    "CG202": "log_broad_except",
    "CG203": "sentinel_default",
    "CG204": "identity_comparison",
    "CG206": "context_manager",
    "CG207": "explicit_validation",
    "CG402": "strip_trailing_whitespace",
    "CG403": "drop_unused_import",
}

#: 不可自动修复的规则 → 为什么要人来看
NOT_AUTOFIXABLE: dict[str, str] = {
    "CG101": "动态执行是不是业务上真需要，只能人判断",
    "CG102": "修命令注入得改调用方的参数约定，等于重新设计",
    "CG103": "反序列化格式一改，存量数据能不能读得另外评估",
    "CG104": "关掉 TLS 校验有时是内网自签证书的临时办法，得先确认 CA 方案",
    "CG107": "随机数用在哪儿（令牌/盐/采样）只有作者知道，不能瞎换",
    "CG108": "临时文件竞态要看并发模型和清理策略，代码里看不出来",
    "CG201": "裸 except 想吞掉什么异常只有作者清楚，改了就把错误语义变了",
    "CG205": "除数为 0 一般是上游数据脏，应该从源头修",
    "CG208": "在 finally 里 return 属控制流结构问题，不是改一行能解决的",
    "CG301": "函数太长要重新划分职责，这是设计决策",
    "CG302": "嵌套太深要抽函数，模块结构会跟着变",
    "CG303": "参数太多要引入聚合对象，对外签名就变了",
    "CG304": "docstring 得写作者的真实意图，机器编出来的注释没意义",
    "CG305": "常量提出来叫什么名字、放哪儿，需要领域知识",
    "CG307": "返回类型得作者确认，注解猜错比不写更糟",
    "CG401": "折行位置影响可读性，得人来定",
    "CG405": "语法错误只能作者自己修，机器猜不出原本想写什么",
    "CG000": "元信息提示，不是代码问题",
}


@dataclass(frozen=True)
class Fixability:
    """一条规则的可修复性结论。"""

    rule_id: str
    fixable: bool
    strategy: str = ""
    reason: str = ""


def classify(rule_id: str) -> Fixability:
    """按规则 ID 判定可修复性。未知规则一律判不可修，交给人工。"""
    normalized = (rule_id or "").strip().upper()
    strategy = FIXABLE_STRATEGIES.get(normalized)
    if strategy:
        return Fixability(rule_id=normalized, fixable=True, strategy=strategy)
    reason = NOT_AUTOFIXABLE.get(normalized) or "未知规则，没有对应的自动改写策略，转人工确认"
    return Fixability(rule_id=normalized, fixable=False, reason=reason)


def is_fixable(rule_id: str) -> bool:
    return classify(rule_id).fixable


def strategy_of(rule_id: str) -> str:
    return classify(rule_id).strategy


def table() -> list[dict[str, object]]:
    """CLI 的 rules 命令和文档用的完整对照表。"""
    rows: list[dict[str, object]] = []
    for rule_id, strategy in sorted(FIXABLE_STRATEGIES.items()):
        rows.append({"rule_id": rule_id, "fixable": True, "strategy": strategy, "reason": ""})
    for rule_id, reason in sorted(NOT_AUTOFIXABLE.items()):
        rows.append({"rule_id": rule_id, "fixable": False, "strategy": "", "reason": reason})
    return rows
