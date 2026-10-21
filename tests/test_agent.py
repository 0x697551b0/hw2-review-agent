"""ReviewAgent 的端到端测试（static 模式，不联网）。

这组用例要证明审查 Agent 真的把 codeguard 的规则引擎跑起来了，结论也能通过契约
交给下游。
"""

from __future__ import annotations

from review_agent.agent import ReviewAgent
from review_agent.config import ReviewConfig
from review_agent.errors import CodeGuardNotFound, NoTargetsError, ReviewError
from review_agent.locate import find_codeguard_root
from review_agent.protocol import AgentRequest

from tests.support import (
    BUGGY_SOURCE,
    CLEAN_SOURCE,
    expect_raises,
    tempdir,
    write,
)


def _agent(**overrides) -> ReviewAgent:
    config = ReviewConfig(mode="static", parallel=1)
    for key, value in overrides.items():
        setattr(config, key, value)
    return ReviewAgent(config)


def test_codeguard_is_locatable() -> None:
    """四个仓库放在同一目录时应能自动发现 codeguard。"""
    root = find_codeguard_root()
    assert root is not None, (
        "找不到 codeguard。请把 Homework 1 的 codeguard 仓库与本仓库放在同一目录下。"
    )
    assert (root / "codeguard" / "__init__.py").is_file()


def test_review_finds_seeded_rules() -> None:
    with tempdir() as tmp:
        write(tmp / "buggy.py", BUGGY_SOURCE)
        outcome = _agent().review(tmp)
    rules = {finding.rule_id for finding in outcome.findings}
    for expected in ("CG106", "CG203", "CG207", "CG202", "CG206", "CG403"):
        assert expected in rules, f"未检出 {expected}，实际命中：{sorted(rules)}"


def test_gate_fails_on_blocking() -> None:
    with tempdir() as tmp:
        write(tmp / "buggy.py", BUGGY_SOURCE)
        outcome = _agent().review(tmp)
    assert not outcome.passed
    assert outcome.metrics["blocking"] > 0
    assert outcome.metrics["overall_risk"] in {"critical", "high"}


def test_clean_code_passes_gate() -> None:
    with tempdir() as tmp:
        write(tmp / "clean.py", CLEAN_SOURCE)
        outcome = _agent().review(tmp)
    assert outcome.passed, [f.rule_id for f in outcome.findings]
    assert outcome.metrics["blocking"] == 0


def test_blocking_level_is_configurable() -> None:
    with tempdir() as tmp:
        write(tmp / "buggy.py", BUGGY_SOURCE)
        loose = _agent().review(tmp, blocking=("critical",))
    # 只把 critical 当阻断级别时，high 级问题不阻断
    assert loose.metrics["blocking"] == sum(
        1 for finding in loose.findings if finding.severity == "critical"
    )


def test_patterns_limit_scope() -> None:
    with tempdir() as tmp:
        write(tmp / "buggy.py", BUGGY_SOURCE)
        write(tmp / "notes.md", "# 不是代码\n")
        outcome = _agent().review(tmp, patterns=("*.md",))
    assert outcome.files_reviewed == ["notes.md"], outcome.files_reviewed


def test_no_targets_raises() -> None:
    with tempdir() as tmp:
        expect_raises(NoTargetsError, _agent().review, tmp)


def test_missing_target_raises() -> None:
    expect_raises(FileNotFoundError, _agent().review, "/definitely/not/here")


def test_max_files_guard() -> None:
    with tempdir() as tmp:
        for index in range(3):
            write(tmp / f"m{index}.py", "x = 1\n")
        expect_raises(ReviewError, _agent(max_files=2).review, tmp)


def test_payload_shape_is_contract_complete() -> None:
    with tempdir() as tmp:
        write(tmp / "buggy.py", BUGGY_SOURCE)
        payload = _agent().review(tmp).payload()
    for key in ("findings", "counts", "passed", "blocking", "score", "files_reviewed", "summary"):
        assert key in payload, key
    assert isinstance(payload["findings"], list)
    assert payload["findings"][0]["finding_id"]


def test_handle_roundtrip() -> None:
    with tempdir() as tmp:
        write(tmp / "buggy.py", BUGGY_SOURCE)
        request = AgentRequest(
            agent="review",
            task_id="review-r1",
            payload={"target": str(tmp), "blocking": ["critical", "high"]},
        )
        response = _agent().handle(request)
    assert response.ok, response.error
    assert response.payload["passed"] is False
    assert response.metrics["findings"] > 0


def test_handle_writes_reports() -> None:
    with tempdir() as tmp:
        write(tmp / "buggy.py", BUGGY_SOURCE)
        report_dir = tmp / "reports"
        request = AgentRequest(
            agent="review",
            task_id="review-r1",
            payload={"target": str(tmp), "report_dir": str(report_dir)},
        )
        response = _agent().handle(request)
        assert response.ok, response.error
        assert (report_dir / "review.json").is_file()
        assert (report_dir / "review.md").is_file()
        assert {a.kind for a in response.artifacts} >= {"review_report", "review_markdown"}


def test_handle_rejects_wrong_agent() -> None:
    request = AgentRequest(agent="fix", task_id="t", payload={"target": "."})
    response = _agent().handle(request)
    assert not response.ok
    assert "agent" in response.error


def test_handle_requires_target() -> None:
    request = AgentRequest(agent="review", task_id="t", payload={})
    response = _agent().handle(request)
    assert not response.ok
    assert "target" in response.error


def test_markdown_report_mentions_rules() -> None:
    with tempdir() as tmp:
        write(tmp / "buggy.py", BUGGY_SOURCE)
        outcome = _agent().review(tmp)
    assert "CG106" in outcome.markdown
    assert "代码审查报告" in outcome.markdown


def test_summary_is_one_line() -> None:
    with tempdir() as tmp:
        write(tmp / "buggy.py", BUGGY_SOURCE)
        outcome = _agent().review(tmp)
    assert "\n" not in outcome.summary(), outcome.summary()
    assert "门禁" in outcome.summary()


def test_missing_codeguard_message_is_actionable() -> None:
    """找不到 codeguard 时的报错必须告诉人怎么修。"""
    message = str(CodeGuardNotFound("找不到 codeguard 包"))
    assert "codeguard" in message


def test_worker_stdout_mode_survives_non_ascii() -> None:
    """
    回归测试：worker 的 stdout 模式必须能输出中文和 emoji。

    这条路径以前是坏的：cli.py 会把 stdout 重配成 UTF-8，worker.py 漏了这一步，
    Windows 控制台（GBK）下 summary 里的 emoji 直接抛 UnicodeEncodeError，退出码 1。
    编排器默认用 --response-file，不走 stdout，所以正常流程里根本暴露不出来，只有
    显式测 stdout 才拦得住。
    """
    import json
    import subprocess
    import sys

    from tests.support import ROOT

    with tempdir() as tmp:
        write(tmp / "buggy.py", BUGGY_SOURCE)
        request = {
            "agent": "review",
            "task_id": "worker-stdout",
            "run_id": "r1",
            "round": 1,
            "payload": {"target": str(tmp), "blocking": ["critical", "high"]},
            "shared": {},
            "metadata": {},
        }
        request_file = write(tmp / "req.json", json.dumps(request, ensure_ascii=False))

        completed = subprocess.run(
            [sys.executable, "-m", "review_agent.worker", "--request-file", str(request_file)],
            cwd=str(ROOT),
            capture_output=True,
            timeout=180,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")[-500:]
    payload = json.loads(completed.stdout.decode("utf-8"))  # 必须是合法 UTF-8
    assert payload["status"] == "ok"
    assert payload["payload"]["findings"], "应当真的审出问题"


def test_cli_missing_path_returns_2() -> None:
    """
    回归测试：CLI 遇到不存在的路径应当给一行错误 + 退出码 2，别让用户看 traceback。

    FileNotFoundError 是内置异常，不会被 ReviewAgentError 接住，这条分支曾经漏过，
    路径打错时用户看到的是一整屏堆栈。
    """
    from review_agent.cli import main

    assert main(["review", "/definitely/not/here/at/all"]) == 2
