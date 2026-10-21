"""
可修复性判定：哪些规则命中可以安全地自动修，哪些必须转人工。

这份表是**审查 Agent 与修复 Agent 之间共享的词汇表**（编排器文档
``docs/CONTRACT.md`` 里有同一张表）。放在审查侧的原因是：

- 审查 Agent 最清楚"这条问题是哪一个规则命中的"，它来定性最自然；
- 编排器与报告需要在一开始就告诉人"这 15 条里 8 条能自动修、7 条要人看"，
  而不是等修复 Agent 试完才知道；
- 修复 Agent 只对自己声明支持的策略负责，两边对不上时能立刻发现（有契约测试）。

判"可修"的标准有三条，缺一不可：

1. **改写是局部的**：不改变函数签名、不改变对外契约；
2. **改写是确定的**：同一处代码永远得到同一个正确结果，不需要业务判断；
3. **改写可验证**：改完能重新扫描并证明该问题消失，且没引入新的阻断级问题。

凡是不满足的（例如"函数过长"需要重新划分职责），一律标成不可修 —— 让 Agent 假装
能修，比明确说"这需要人"危害更大。
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
    "CG101": "需要判断这段字符串的真实语义（是否必须动态执行），属业务决策",
    "CG102": "命令注入的修复要重新设计参数传递方式，涉及调用方契约",
    "CG103": "反序列化格式变更会影响持久化数据兼容性，需评估迁移",
    "CG104": "关闭 TLS 校验有时是内网自签证书的权宜之计，需确认 CA 方案",
    "CG107": "随机数用途（令牌 / 盐 / 采样）只有作者知道，不能盲目替换",
    "CG108": "临时文件竞态需要确认并发模型与清理策略",
    "CG201": "裸 except 想吞掉什么异常只有作者清楚，自动改会改变错误语义",
    "CG205": "除数为 0 通常说明上游数据有问题，应在数据源头修",
    "CG208": "finally 里 return 的意图需要重排控制流，属结构性改动",
    "CG301": "函数过长需要重新划分职责，属设计决策",
    "CG302": "嵌套过深需要提取函数，会改变模块结构",
    "CG303": "参数过多需要引入聚合对象，会改变对外签名",
    "CG304": "文档字符串必须描述真实意图，机器生成的注释没有价值",
    "CG305": "提取常量的命名与归属需要领域知识",
    "CG307": "返回值类型需作者确认，猜错注解比没有注解更糟",
    "CG401": "折行位置影响可读性，需要人工斟酌",
    "CG405": "语法错误需作者修复，机器无法推断原本想写什么",
    "CG000": "这是「结果被截断」的元信息，不是代码问题",
}


@dataclass(frozen=True)
class Fixability:
    """一条规则的可修复性结论。"""

    rule_id: str
    fixable: bool
    strategy: str = ""
    reason: str = ""


def classify(rule_id: str) -> Fixability:
    """按规则 ID 判定可修复性。未知规则一律判为不可修（宁可保守）。"""
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
    """供 CLI / 文档生成的完整对照表。"""
    rows: list[dict[str, object]] = []
    for rule_id, strategy in sorted(FIXABLE_STRATEGIES.items()):
        rows.append({"rule_id": rule_id, "fixable": True, "strategy": strategy, "reason": ""})
    for rule_id, reason in sorted(NOT_AUTOFIXABLE.items()):
        rows.append({"rule_id": rule_id, "fixable": False, "strategy": "", "reason": reason})
    return rows
