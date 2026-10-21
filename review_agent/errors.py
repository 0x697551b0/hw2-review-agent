"""审查 Agent 的异常体系。

最特殊的一条是 :class:`CodeGuardNotFound`：审查 Agent 的定位就是"把 codeguard 接进
工作流"，因此 codeguard 缺失必须给出**可执行的修复建议**，而不是一句
``ModuleNotFoundError``。
"""

from __future__ import annotations


class ReviewAgentError(Exception):
    """本 Agent 所有异常的基类。"""


class CodeGuardNotFound(ReviewAgentError):
    """找不到 codeguard 包（既没安装，也没在附近找到源码目录）。"""


class ReviewError(ReviewAgentError):
    """审查过程失败（目标目录不存在、模型调用失败等）。"""


class NoTargetsError(ReviewError):
    """没有可审查的目标文件（例如目标目录里没有受支持的源码）。"""
