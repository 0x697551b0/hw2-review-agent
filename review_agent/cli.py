"""审查 Agent 的命令行。

review 审查目录，默认打印可读报告，--json 给契约 JSON；doctor 查 codeguard 找不
找得到、版本多少、从哪加载的，环境出问题先跑它；rules 打印可修复性对照表；info
打版本和配置。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from review_agent import __version__
from review_agent.agent import ReviewAgent
from review_agent.config import ReviewConfig
from review_agent.errors import ReviewAgentError
from review_agent.locate import describe as describe_codeguard
from review_agent.protocol import dumps

BANNER = r"""
  ____                 _
 |  _ \ _____   ____ _(_)_   _____ _ __
 | |_) / _ \ \ / / _` | \ \ / / _ \ '__|
 |  _ <  __/\ V / (_| | |\ V /  __/ |
 |_| \_\___| \_/ \__,_|_| \_/ \___|_|    审查 Agent · Homework 2
""".strip("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-agent",
        description="审查 Agent：把 codeguard 接入工作流，产出契约化的审查结论与门禁判定",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python -m review_agent review ./worktree\n"
            "  python -m review_agent review ./worktree --json --report-dir ./reports/r1\n"
            "  python -m review_agent review ./worktree --blocking critical\n"
            "  python -m review_agent review ./worktree --mode llm --mock\n"
            "  python -m review_agent doctor\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"review-agent {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    review_cmd = sub.add_parser("review", help="审查目录或文件")
    review_cmd.add_argument("target", help="要审查的目录或文件")
    review_cmd.add_argument("--mode", choices=("static", "llm"), help="审查模式（默认 static）")
    review_cmd.add_argument("--mock", action="store_true", help="mode=llm 时用离线脚本化模型")
    review_cmd.add_argument("--pattern", action="append", dest="patterns", help="文件 glob，可重复")
    review_cmd.add_argument(
        "--blocking", action="append", help="阻断级别，可重复；默认 critical + high"
    )
    review_cmd.add_argument("--rules", action="append", help="只跑指定规则 ID，可重复")
    review_cmd.add_argument("--parallel", type=int, help="并行审查文件数")
    review_cmd.add_argument("--codeguard-path", help="codeguard 源码根目录")
    review_cmd.add_argument("--api-key", help="codeguard 的 API Key（mode=llm）")
    review_cmd.add_argument("--model", help="模型名（mode=llm）")
    review_cmd.add_argument("--report-dir", help="把 review.json / review.md 写到该目录")
    review_cmd.add_argument("--json", action="store_true", help="以 JSON 输出契约结果")
    review_cmd.add_argument("--quiet", action="store_true", help="只输出一行结论")
    review_cmd.add_argument("--fail-on-blocking", action="store_true", help="存在阻断级问题时返回退出码 1")
    review_cmd.add_argument("--verbose", action="store_true")

    sub.add_parser("doctor", help="检查 codeguard 定位情况与配置")
    sub.add_parser("rules", help="打印可修复性对照表")
    sub.add_parser("info", help="打印版本与配置")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args)

    if not args.command or args.command == "info":
        print(BANNER)
        print(f"\n版本：{__version__}    环境：Python {sys.version.split()[0]}")
        print(f"配置：{ReviewConfig.from_env().describe()}")
        print("\n可用命令：review / doctor / rules / info")
        return 0

    if args.command == "doctor":
        return _cmd_doctor()
    if args.command == "rules":
        return _cmd_rules()

    try:
        return _cmd_review(args)
    except ReviewAgentError as exc:
        print(f"❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        # 目标路径不存在是调用方输入问题，给一行清晰的错误就够了。这条分支曾经漏掉过：
        # FileNotFoundError 是内置异常，不会被 ReviewAgentError 接住，路径打错就是整屏堆栈。
        print(f"❌ 找不到路径：{exc}", file=sys.stderr)
        return 2


def _cmd_review(args: argparse.Namespace) -> int:
    config = _build_config(args)
    agent = ReviewAgent(config)
    outcome = agent.review(
        args.target,
        patterns=args.patterns,
        blocking=args.blocking,
    )

    if args.report_dir:
        directory = Path(args.report_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "review.json").write_text(dumps(outcome.payload()), encoding="utf-8", newline="\n")
        if outcome.markdown:
            (directory / "review.md").write_text(outcome.markdown, encoding="utf-8", newline="\n")

    if args.json:
        print(dumps(outcome.payload()))
    elif args.quiet:
        print(outcome.summary())
    else:
        print(outcome.markdown or outcome.summary())
        print("\n" + "─" * 68)
        print(f"结论：{outcome.summary()}")
        print(f"耗时：{outcome.elapsed_seconds:.2f}s    可自动修 {outcome.metrics['fixable']} 条 / 需人工 {outcome.metrics['needs_human']} 条")

    if args.fail_on_blocking and not outcome.passed:
        return 1
    return 0


def _cmd_doctor() -> int:
    print(BANNER)
    print("\n【codeguard 定位】")
    info = describe_codeguard()
    if info["available"]:
        print(f"  ✅ 可用    版本：{info['version']}")
        print(f"     来源：{info['file']}")
    else:
        print("  ❌ 不可用")
        print(f"     {info['error']}")
        print(f"     已搜索 {len(info['searched'])} 个候选根目录，前 10 个：")
        for root in info["searched"][:10]:
            print(f"       - {root}")
    print("\n【配置】")
    print(f"  {ReviewConfig.from_env().describe()}")
    return 0 if info["available"] else 1


def _cmd_rules() -> int:
    from review_agent.fixability import table

    rows = table()
    print(f"{'规则':<8}{'可自动修':<10}{'策略':<28}说明")
    print("─" * 100)
    for row in rows:
        auto = "✅" if row["fixable"] else "—"
        print(f"{row['rule_id']:<8}{auto:<10}{row['strategy'] or '-':<28}{row['reason'] or ''}")
    print(f"\n共 {len(rows)} 条规则：{sum(1 for r in rows if r['fixable'])} 条可自动修复。")
    return 0


def _build_config(args: argparse.Namespace) -> ReviewConfig:
    overrides: dict[str, object] = {}
    for key in ("mode", "codeguard_path", "api_key", "model", "parallel"):
        value = getattr(args, key, None)
        if value:
            overrides[key] = value
    if getattr(args, "mock", False):
        overrides["mock"] = True
    if getattr(args, "rules", None):
        overrides["rules"] = tuple(args.rules)
    if getattr(args, "blocking", None):
        overrides["blocking"] = tuple(args.blocking)
    config = ReviewConfig.from_env(**overrides)
    config.validate()
    return config


def _setup_logging(args: argparse.Namespace) -> None:
    level = logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    _ensure_utf8_stdout()


def _ensure_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，遇到中文/emoji 会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
