"""审查 Agent 的配置。环境变量前缀 REVIEW_。

blocking（哪些级别算不合格）是配置项，不是写死的常量："high 及以上不算合格"是
团队的质量策略，早期只挡 critical、发版前收紧到 high，改配置就行，不用动代码。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from review_agent.errors import ReviewError
from review_agent.protocol import SEVERITY_ORDER

ENV_PREFIX = "REVIEW_"

#: 审查模式：``static`` 用确定性规则引擎；``llm`` 用 codeguard 的 Agent 闭环
MODES: tuple[str, ...] = ("static", "llm")


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> dict[str, str]:
    """极简 .env 解析，和 codeguard 那边保持同一份实现。"""
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    parsed: dict[str, str] = {}
    for raw_line in file_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key:
            continue
        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


@dataclass
class ReviewConfig:
    """审查 Agent 运行期配置。"""

    # ---- 审查策略 ----
    #: ``static`` / ``llm``
    mode: str = "static"
    #: 阻断级别（低级别不阻断流水线）
    blocking: tuple[str, ...] = ("critical", "high")
    patterns: tuple[str, ...] = ("*.py",)
    parallel: int = 4
    #: 单次审查最多处理多少个文件（防止误指到超大目录）
    max_files: int = 200
    rules: tuple[str, ...] = ()

    # ---- 输出 ----
    workspace_root: str = ""
    #: codeguard 源码根目录（可选，不填则自动发现）
    codeguard_path: str = ""

    # ---- LLM（仅 mode=llm 时使用，透传给 codeguard）----
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0.2
    max_iterations: int = 10
    timeout: float = 120.0
    #: 用 codeguard 的脚本化模型离线走一遍 Agent 闭环（不访问网络）
    mock: bool = False

    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, dotenv_path: str | Path | None = ".env", **overrides: Any) -> "ReviewConfig":
        if dotenv_path is not None:
            load_dotenv(dotenv_path)
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            env_name = f"{ENV_PREFIX}{f.name.upper()}"
            if env_name not in os.environ:
                continue
            kwargs[f.name] = _coerce(os.environ[env_name], f.type, f.name)
        kwargs.update({k: v for k, v in overrides.items() if v is not None})
        config = cls(**kwargs)
        # 兼容 Homework 1 的变量名：mode=llm 时透传给 codeguard，同一个 .env 两个项目都能用
        if not config.api_key.strip():
            config.api_key = os.environ.get("CODEGUARD_API_KEY", "").strip()
        if config.base_url == cls().base_url:
            config.base_url = os.environ.get("CODEGUARD_BASE_URL", "").strip() or config.base_url
        if config.model == cls().model:
            config.model = os.environ.get("CODEGUARD_MODEL", "").strip() or config.model
        return config

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ReviewError(f"mode 只能取 {MODES}，当前为 {self.mode!r}")
        unknown = [s for s in self.blocking if s not in SEVERITY_ORDER]
        if unknown:
            raise ReviewError(
                f"blocking 含有未知级别 {unknown}，可选取值：{', '.join(SEVERITY_ORDER)}"
            )
        if not self.blocking:
            raise ReviewError("blocking 不能为空；若想不阻断，请显式配置为 ('critical',)")
        if self.parallel < 1:
            raise ReviewError("parallel 至少为 1")
        if self.max_files < 1:
            raise ReviewError("max_files 至少为 1")
        if self.mode == "llm" and not self.mock and not self.api_key.strip():
            raise ReviewError(
                "mode=llm 需要 codeguard 的 API Key。任选一种方式：\n"
                "  1) 环境变量 CODEGUARD_API_KEY=sk-xxxx（沿用 Homework 1 的变量名）\n"
                f"  2) 环境变量 {ENV_PREFIX}API_KEY=sk-xxxx\n"
                "  3) 命令行 --api-key sk-xxxx\n"
                "  4) 无 Key：用 --mode static 走确定性规则引擎，或加 --mock 走离线闭环"
            )

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace_root).resolve() if self.workspace_root else Path.cwd().resolve()

    def describe(self) -> str:
        masked = "未配置"
        if self.mock:
            masked = "离线脚本化模型"
        elif self.api_key:
            masked = f"{self.api_key[:8]}...{self.api_key[-4:]}" if len(self.api_key) > 16 else "***"
        return (
            f"mode={self.mode}  blocking={','.join(self.blocking)}  "
            f"patterns={','.join(self.patterns)}  parallel={self.parallel}  "
            f"model={self.model}  api_key={masked}"
        )


def _coerce(raw: str, type_hint: Any, name: str) -> Any:
    """把环境变量的字符串转成字段声明的类型（含 tuple 的逗号分隔写法）。"""
    hint = type_hint if isinstance(type_hint, str) else getattr(type_hint, "__name__", "str")
    text = raw.strip()
    if hint == "bool":
        return text.lower() in {"1", "true", "yes", "y", "on"}
    if hint == "int":
        try:
            return int(text)
        except ValueError as exc:
            raise ReviewError(f"环境变量 {ENV_PREFIX}{name.upper()} 应为整数，实际为 {raw!r}") from exc
    if hint == "float":
        try:
            return float(text)
        except ValueError as exc:
            raise ReviewError(f"环境变量 {ENV_PREFIX}{name.upper()} 应为小数，实际为 {raw!r}") from exc
    if hint.startswith("tuple"):
        return tuple(part.strip() for part in text.split(",") if part.strip())
    return text
