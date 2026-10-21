"""找 codeguard。整个仓库对它的依赖都收在这个文件里。

Homework 2 的四个仓库是各自独立的，clone 到同一个目录下协作。要求评审者先跑
pip install -e ../codeguard 才能用，等于给可用性挖坑：网络不通、虚拟环境不对、
系统 Python 被锁，哪一样都会让"一条命令跑起来"变成奢望。

所以按 已装好的包 → HW2_PACKAGES_HOME → --codeguard-path → 附近目录 的顺序找，
找到后只把那一层插进 sys.path，不写 site-packages，进程退出就失效。

最后一条覆盖面最广：无论从哪个仓库里执行命令，都能找到隔壁的 codeguard。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

from review_agent.errors import CodeGuardNotFound

#: 环境变量：额外的包搜索根目录（用 os.pathsep 分隔）
ENV_PACKAGES_HOME = "HW2_PACKAGES_HOME"

#: 期望的包名
PACKAGE_NAME = "codeguard"

#: 向上搜索的最大层数
_MAX_ANCESTORS = 5


def ensure_codeguard(*, explicit: str | Path | None = None, required_version: str = "") -> ModuleType:
    """确保 codeguard 能 import，返回模块对象。

    explicit 是显式给的源码根目录（下面得有 codeguard/__init__.py）；
    required_version 给定时校验最低版本。
    """
    module = _try_import()
    if module is None:
        for root in _candidate_roots(explicit):
            if _has_package(root):
                _prepend_sys_path(root)
                module = _try_import()
                if module is not None:
                    break
    if module is None:
        raise CodeGuardNotFound(_not_found_message(explicit))

    if required_version:
        version = getattr(module, "__version__", "")
        if version and _version_tuple(version) < _version_tuple(required_version):
            raise CodeGuardNotFound(
                f"找到的 codeguard 版本为 {version}，低于需要的 {required_version}。"
                "请更新 codeguard（Homework 1 的仓库）后重试。"
            )
    return module


def find_codeguard_root(*, explicit: str | Path | None = None) -> Path | None:
    """只做定位，不 import。用于 ``doctor`` 命令与错误信息展示。"""
    for root in _candidate_roots(explicit):
        if _has_package(root):
            return root
    return None


def describe() -> dict[str, object]:
    """诊断信息：安装状态、版本、来源路径。"""
    try:
        module = ensure_codeguard()
    except CodeGuardNotFound as exc:
        return {"available": False, "error": str(exc), "searched": [str(p) for p in _candidate_roots(None)]}
    path = getattr(module, "__file__", "") or ""
    return {
        "available": True,
        "version": getattr(module, "__version__", "unknown"),
        "file": path,
        "search_roots": len(set(_candidate_roots(None))),
    }


# 内部实现
def _try_import() -> ModuleType | None:
    try:
        import importlib

        return importlib.import_module(PACKAGE_NAME)
    except Exception:  # noqa: BLE001 - 导入失败可能是任何原因，统一当作"没找到"
        return None


def _has_package(root: Path) -> bool:
    try:
        return (root / PACKAGE_NAME / "__init__.py").is_file()
    except OSError:  # pragma: no cover
        return False


def _prepend_sys_path(root: Path) -> None:
    """把 root 插到 sys.path 最前（幂等，且不重复插入）。"""
    entry = str(root.resolve())
    # 先清掉 sys.modules 里可能残留的"半加载"状态，避免拿到空模块
    sys.modules.pop(PACKAGE_NAME, None)
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)
    # 让后续 import 找到新路径上的包（importlib 缓存了路径查找器）
    import importlib

    importlib.invalidate_caches()


def _candidate_roots(explicit: str | Path | None) -> list[Path]:
    """按优先级生成候选根目录，去重且保序。"""
    roots: list[Path] = []

    def add(candidate: Path | str | None) -> None:
        if candidate is None:
            return
        path = Path(candidate).expanduser()
        try:
            resolved = path.resolve()
        except OSError:  # pragma: no cover
            return
        if resolved not in roots:
            roots.append(resolved)

    if explicit:
        add(Path(explicit))
        # 允许把"包目录本身"传进来（即 ...\codeguard 里面还有 codeguard 包）
        if explicit is not None:
            add(Path(explicit).parent)

    for entry in (os.environ.get(ENV_PACKAGES_HOME) or "").split(os.pathsep):
        if entry.strip():
            add(entry.strip())

    current = Path.cwd()
    _walk_up(current, add)
    _walk_up(Path(__file__).resolve().parents[1], add)
    return roots


def _walk_up(start: Path, add) -> None:
    """从 start 逐级向上，把每一级本身与它的直接子目录都当作候选根。"""
    node = start
    for _ in range(_MAX_ANCESTORS):
        add(node)
        for child in _safe_subdirs(node):
            add(child)
        if node.parent == node:
            break
        node = node.parent


def _safe_subdirs(root: Path) -> list[Path]:
    try:
        return [p for p in root.iterdir() if p.is_dir()]
    except OSError:  # pragma: no cover - 权限受限的目录直接跳过
        return []


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(text).replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _not_found_message(explicit: str | Path | None) -> str:
    lines = [
        "找不到 codeguard 包，审查 Agent 无法工作。",
        "本 Agent 的定位是把 Homework 1 的 codeguard 接入工作流，因此它是必需依赖。",
        "",
        "任选一种方式解决：",
        "  1) 把 codeguard 仓库与本仓库放在同一个目录下（推荐，无需安装）：",
        "       workspace/",
        "         codeguard/            <- Homework 1 的仓库（含 codeguard/__init__.py）",
        "         hw2-review-agent/     <- 本仓库",
        "     然后在任意目录直接运行本 Agent 即可，会自动发现。",
        "  2) 显式指定：python -m review_agent review . --codeguard-path /path/to/codeguard",
        "  3) 设置环境变量：HW2_PACKAGES_HOME=/path/to/packages",
        "  4) 安装：pip install -e /path/to/codeguard",
        "",
        "来源：https://github.com/0x697551b0/codeguard",
    ]
    if explicit:
        lines.insert(1, f"（已尝试显式路径：{explicit}，但该目录下没有 codeguard/__init__.py）")
    return "\n".join(lines)
