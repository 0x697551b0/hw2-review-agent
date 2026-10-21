"""python -m review_agent.worker：子进程模式的一次 JSON 往返。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from review_agent.agent import AGENT_NAME, ReviewAgent
from review_agent.config import ReviewConfig
from review_agent.protocol import AgentRequest, AgentResponse, dumps


def main(argv: Sequence[str] | None = None) -> int:
    # stdout 模式下得先把编码切到 UTF-8：Windows 控制台默认 GBK，summary 里带
    # ❌/✅ 这类符号，直接 print 就是 UnicodeEncodeError。编排器默认走
    # --response-file，这条路径平时跑不到，所以更要显式测。
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python -m review_agent.worker",
        description="以子进程方式执行一次审查任务（stdin → stdout 单次 JSON 往返）",
    )
    parser.add_argument("--request-file", help="请求 JSON 文件；省略则从 stdin 读取")
    parser.add_argument("--response-file", help="响应写到文件；省略则写到 stdout")
    parser.add_argument("--mode", choices=("static", "llm"), help="覆盖请求中的审查模式")
    parser.add_argument("--codeguard-path", help="codeguard 源码根目录")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        raw = Path(args.request_file).read_text(encoding="utf-8-sig") if args.request_file else sys.stdin.read()
        payload = json.loads(raw)
    except (OSError, ValueError) as exc:
        return _emit(AgentResponse.failure(AGENT_NAME, f"无法读取请求 JSON：{exc}"), args.response_file)

    try:
        request = AgentRequest.from_dict(payload)
    except ValueError as exc:
        return _emit(AgentResponse.failure(AGENT_NAME, f"请求格式错误：{exc}"), args.response_file)

    overrides: dict[str, object] = {}
    if args.mode:
        overrides["mode"] = args.mode
    if args.codeguard_path:
        overrides["codeguard_path"] = args.codeguard_path
    # 只把配置类真正认识的键传进去：多传一个未知键会直接 TypeError，
    # 而子进程里的 TypeError 对调用方只表现为"没有产出响应"，非常难查
    overrides = _filter_known(ReviewConfig, overrides)

    config = ReviewConfig.from_env(**overrides)
    try:
        config.validate()
        agent = ReviewAgent(config)
    except Exception as exc:  # noqa: BLE001 - 初始化失败也要变成合法响应
        return _emit(AgentResponse.failure(AGENT_NAME, f"初始化失败：{exc}"), args.response_file)

    response = agent.handle(request)
    return _emit(response, args.response_file)


def _filter_known(config_cls: type, values: dict[str, object]) -> dict[str, object]:
    """过滤掉配置类不接受的键。"""
    from dataclasses import fields

    known = {f.name for f in fields(config_cls)}
    return {key: value for key, value in values.items() if key in known}


def _emit(response: AgentResponse, response_file: str | None) -> int:
    text = dumps(response.to_dict())
    if response_file:
        try:
            Path(response_file).write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:  # pragma: no cover
            print(f"无法写入响应文件：{exc}", file=sys.stderr)
            return 1
    else:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    if not response.ok:
        print(f"审查 Agent 执行失败：{response.error}", file=sys.stderr)
        return 1
    return 0


def _ensure_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，输出中文/emoji 会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
