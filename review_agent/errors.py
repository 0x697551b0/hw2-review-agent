"""异常体系。

CodeGuardNotFound 单独一个类，因为它要带一条照着做就能修好的提示，光抛一句
ModuleNotFoundError 没用。
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
