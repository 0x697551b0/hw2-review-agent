"""
ReviewAgent：把 Homework 1 的 codeguard 变成一个工作流节点。

这个 Agent 本身刻意做得很"薄"，它提供的是**四件 codeguard 单独做不到的事**：

1. **可定位**：不要求预先 ``pip install``，同目录下放着就能用（见 ``locate.py``）；
2. **可契约化**：把 codeguard 的 ``ReviewReport`` 归一化成工作流契约，并给每条
   发现分配跨轮次稳定的 ID（见 ``normalize.py``）；
3. **可门禁**：把"哪些级别算不合格"变成显式配置，输出 ``passed`` 与可解释的判定
   明细，供编排器做条件分支；
4. **可并行**：静态审查按文件 fan-out，文件数多时线性缩短墙钟时间。

两种模式：

- ``static``：只跑确定性规则引擎。快、零成本、结果可复现 —— 适合放进循环反复跑；
- ``llm``：跑 codeguard 的完整 Agent 闭环（推理 → 工具调用 → 观察 → 报告），
  能发现规则覆盖不到的语义问题。慢、要 Key，但结论更深。加 ``mock=True``
  可用脚本化模型离线体验这条链路。
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

from review_agent.config import ReviewConfig
from review_agent.errors import NoTargetsError, ReviewError
from review_agent.locate import ensure_codeguard
from review_agent.normalize import (
    normalize_findings,
    resolve_targets,
    summarize,
)
from review_agent.protocol import (
    STATUS_OK,
    AgentRequest,
    AgentResponse,
    FindingContract,
    count_by_severity,
    dumps,
)

AGENT_NAME = "review"


@dataclass
class ReviewOutcome:
    """一次审查的完整结果（工作流 payload 的来源）。"""

    findings: list[FindingContract] = field(default_factory=list)
    files_reviewed: list[str] = field(default_factory=list)
    mode: str = "static"
    blocking: tuple[str, ...] = ("critical", "high")
    summary_text: str = ""
    markdown: str = ""
    elapsed_seconds: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)
    #: codeguard 的原始报告（mode=llm 时有模型结论、用量等）
    raw_meta: dict[str, Any] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return count_by_severity(self.findings)

    @property
    def metrics(self) -> dict[str, Any]:
        return summarize(self.findings, blocking=self.blocking)

    @property
    def passed(self) -> bool:
        return bool(self.metrics["passed"])

    def summary(self) -> str:
        """一句话结论。"""
        metrics = self.metrics
        counts = metrics["counts"]
        pieces = [
            f"{counts[sev]} {sev}" for sev in ("critical", "high", "medium", "low", "info") if counts[sev]
        ]
        detail = "、".join(pieces) if pieces else "无问题"
        gate = "✅ 通过门禁" if metrics["passed"] else f"❌ 未通过门禁（{metrics['blocking']} 条阻断级）"
        return (
            f"审查 {len(self.files_reviewed)} 个文件，命中 {metrics['total']} 条（{detail}）；"
            f"{gate}；健康分 {metrics['score']}"
        )

    def payload(self) -> dict[str, Any]:
        metrics = self.metrics
        return {
            "mode": self.mode,
            "blocking_severities": list(self.blocking),
            "findings": [f.to_dict() for f in self.findings],
            "counts": metrics["counts"],
            "total": metrics["total"],
            "blocking": metrics["blocking"],
            "passed": metrics["passed"],
            "fixable": metrics["fixable"],
            "needs_human": metrics["needs_human"],
            "score": metrics["score"],
            "overall_risk": metrics["overall_risk"],
            "files_reviewed": list(self.files_reviewed),
            "summary": self.summary_text or self.summary(),
            "diagnostics": dict(self.diagnostics),
        }


class ReviewAgent:
    """审查 Agent。``codeguard`` 参数可注入（测试用），为空则自动定位。"""

    def __init__(self, config: ReviewConfig | None = None, *, codeguard: ModuleType | None = None) -> None:
        self.config = config or ReviewConfig.from_env()
        self.config.validate()
        self.codeguard = codeguard or ensure_codeguard(
            explicit=self.config.codeguard_path or None
        )

    # 主入口
    def review(
        self,
        target: str | Path,
        *,
        patterns: Sequence[str] | None = None,
        blocking: Sequence[str] | None = None,
    ) -> ReviewOutcome:
        """审查一个目录或文件。"""
        started = time.perf_counter()
        effective_blocking = tuple(blocking or self.config.blocking)
        paths = resolve_targets(
            self._absolute(target),
            patterns=tuple(patterns or self.config.patterns),
        )
        if not paths:
            raise NoTargetsError(
                f"在 {target} 下没有找到匹配 {list(patterns or self.config.patterns)} 的文件"
            )
        if len(paths) > self.config.max_files:
            raise ReviewError(
                f"待审查文件数 {len(paths)} 超过上限 {self.config.max_files}；"
                "请缩小目标范围或调大 max_files"
            )

        if self.config.mode == "llm":
            outcome = self._review_with_llm(paths, effective_blocking)
        else:
            outcome = self._review_static(paths, effective_blocking)

        outcome.elapsed_seconds = round(time.perf_counter() - started, 3)
        outcome.diagnostics.update(
            {
                "mode": self.config.mode,
                "codeguard_version": getattr(self.codeguard, "__version__", "unknown"),
                "files": len(paths),
                "patterns": list(patterns or self.config.patterns),
                "mock": self.config.mock,
            }
        )
        return outcome

    # static 模式
    def _review_static(
        self, paths: Sequence[Path], blocking: tuple[str, ...]
    ) -> ReviewOutcome:
        """确定性规则引擎，按文件并行。"""
        analyzer = self.codeguard.static_analysis.CodeAnalyzer(
            rules=list(self.config.rules) or None
        )

        def analyze(path: Path) -> list[Any]:
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # pragma: no cover - 权限问题
                raise ReviewError(f"无法读取 {path}：{exc}") from exc
            return list(analyzer.analyze_source(source, path=str(path)).findings)

        raw: list[Any] = []
        if self.config.parallel > 1 and len(paths) > 1:
            with ThreadPoolExecutor(max_workers=self.config.parallel) as pool:
                for chunk in pool.map(analyze, paths):
                    raw.extend(chunk)
        else:
            for path in paths:
                raw.extend(analyze(path))

        findings = normalize_findings(raw, root=self._root(paths))
        return ReviewOutcome(
            findings=findings,
            files_reviewed=[self._display(p) for p in paths],
            mode="static",
            blocking=blocking,
            summary_text=(
                f"确定性规则引擎审查 {len(paths)} 个文件，命中 {len(findings)} 条问题；"
                "结果完全可复现，不含语义推断"
            ),
            markdown=self._render_markdown(findings, mode="static"),
            diagnostics={"engine": "codeguard.static_analysis", "parallel": self.config.parallel},
        )

    # llm 模式
    def _review_with_llm(
        self, paths: Sequence[Path], blocking: tuple[str, ...]
    ) -> ReviewOutcome:
        """走 codeguard 的完整 Agent 闭环。"""
        target = paths[0].parent if len(paths) > 1 else paths[0]
        root = self._root(paths)
        cg_config = self._build_codeguard_config(root)
        agent = self.codeguard.CodeReviewAgent(cg_config)
        try:
            result = agent.review(target if len(paths) > 1 else paths[0])
        finally:
            agent.close()

        report = result.report
        findings = normalize_findings(report.findings, root=root)
        # 模型可能给出报告里没覆盖到的问题，但 codeguard 已把静态命中合并进报告，
        # 因此这里不再额外合并，避免同一问题重复计数。
        markdown = ""
        try:
            markdown = self.codeguard.report.render_markdown(report)
        except Exception as exc:  # noqa: BLE001 - 渲染失败不该让审查失败
            markdown = f"（Markdown 渲染失败：{exc}）"

        return ReviewOutcome(
            findings=findings,
            files_reviewed=list(report.files_reviewed) or [self._display(p) for p in paths],
            mode="llm",
            blocking=blocking,
            summary_text=report.summary or "",
            markdown=markdown,
            raw_meta={
                "iterations": result.iterations,
                "tool_calls": result.tool_calls,
                "stopped_reason": result.stopped_reason,
                "usage": result.usage.to_dict(),
                "model": cg_config.model,
                "report_meta": report.meta,
            },
            diagnostics={
                "engine": "codeguard.agent",
                "model": cg_config.model,
                "iterations": result.iterations,
                "stopped_reason": result.stopped_reason,
            },
        )

    def _build_codeguard_config(self, root: Path) -> Any:
        """把本 Agent 的配置翻译成 codeguard 的配置（字段名不同，需显式映射）。"""
        config_cls = self.codeguard.Config
        return config_cls(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
            temperature=self.config.temperature,
            max_iterations=self.config.max_iterations,
            timeout=self.config.timeout,
            mock=self.config.mock,
            workspace_root=str(root),
            extra_headers=dict(self.config.extra_headers),
        )

    # 渲染
    def _render_markdown(self, findings: Sequence[FindingContract], *, mode: str) -> str:
        """生成人类可读的审查报告（契约层自有渲染，不依赖 codeguard 的报告样式）。"""
        counts = count_by_severity(findings)
        lines = [
            "# 代码审查报告",
            "",
            f"- 模式：`{mode}`",
            f"- 问题总数：{len(findings)}",
            "- 分布："
            + " / ".join(f"{sev} {counts[sev]}" for sev in ("critical", "high", "medium", "low", "info")),
            "",
        ]
        if not findings:
            lines.append("✅ 没有命中任何规则。")
            return "\n".join(lines) + "\n"

        lines += ["| # | 级别 | 规则 | 位置 | 问题 | 可自动修 |", "| --- | --- | --- | --- | --- | --- |"]
        for index, finding in enumerate(findings, start=1):
            auto = "✅" if finding.fixable else "—"
            lines.append(
                f"| {index} | {finding.severity} | {finding.rule_id or '-'} | "
                f"`{finding.location}` | {finding.title} | {auto} |"
            )
        needs_human = [f for f in findings if not f.fixable]
        if needs_human:
            lines += ["", "## 需要人工处理", ""]
            for finding in needs_human:
                lines.append(f"- `{finding.location}` **{finding.title}**：{finding.note or finding.suggestion}")
        return "\n".join(lines) + "\n"

    # 辅助
    def _absolute(self, target: str | Path) -> Path:
        path = Path(target)
        return path if path.is_absolute() else self.config.workspace_path / path

    @staticmethod
    def _root(paths: Sequence[Path]) -> Path:
        """归一化基准：取所有目标文件的共同父目录，保证报告里都是相对路径。"""
        if not paths:
            return Path.cwd()
        parents = [str(Path(p).resolve().parent) for p in paths]
        if len(set(parents)) == 1:
            return Path(parents[0])
        common = os.path.commonpath(parents)
        return Path(common or parents[0])

    @staticmethod
    def _display(path: Path) -> str:
        return Path(path).name

    # 协议入口
    def handle(self, request: AgentRequest) -> AgentResponse:
        problems = request.validate(expected_agent=AGENT_NAME)
        if problems:
            return AgentResponse.failure(AGENT_NAME, "请求不合法：" + "；".join(problems))

        payload = request.payload
        target = str(payload.get("target") or payload.get("workdir") or "").strip()
        if not target:
            return AgentResponse.failure(AGENT_NAME, "payload 缺少 target（要审查哪个目录/文件）")

        patterns = payload.get("patterns") or payload.get("targets") or None
        if isinstance(patterns, str):
            patterns = [patterns]
        blocking = payload.get("blocking") or payload.get("blocking_severities") or None
        if isinstance(blocking, str):
            blocking = [blocking]

        try:
            outcome = self.review(target, patterns=patterns, blocking=blocking)
        except (ReviewError, NoTargetsError) as exc:
            return AgentResponse.failure(AGENT_NAME, f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - 协议层兜住一切
            return AgentResponse.failure(AGENT_NAME, f"未预期错误 {type(exc).__name__}: {exc}")

        response = AgentResponse(
            agent=AGENT_NAME,
            status=STATUS_OK,
            summary=outcome.summary(),
            payload=outcome.payload(),
            metrics={
                "elapsed_seconds": outcome.elapsed_seconds,
                "findings": len(outcome.findings),
                "blocking": outcome.metrics["blocking"],
            },
            diagnostics=outcome.diagnostics,
        )

        # 产物落盘：工作流会把它们归档到本轮目录
        out_dir = str(payload.get("report_dir") or "").strip()
        if out_dir:
            self._persist(out_dir, outcome, response)
        return response

    def _persist(self, out_dir: str, outcome: ReviewOutcome, response: AgentResponse) -> None:
        """把 review.json / review.md 写到报告目录，并登记为 artifact。"""
        from review_agent.protocol import Artifact

        directory = Path(out_dir)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "review.json").write_text(
                dumps(outcome.payload()), encoding="utf-8", newline="\n"
            )
            if outcome.markdown:
                (directory / "review.md").write_text(outcome.markdown, encoding="utf-8", newline="\n")
        except OSError:
            return

        response.artifacts.append(
            Artifact(
                kind="review_report",
                path="review.json",
                media_type="application/json",
                description=f"{len(outcome.findings)} 条发现",
            )
        )
        if outcome.markdown:
            response.artifacts.append(
                Artifact(
                    kind="review_markdown",
                    path="review.md",
                    media_type="text/markdown",
                    description="人类可读审查报告",
                )
            )
