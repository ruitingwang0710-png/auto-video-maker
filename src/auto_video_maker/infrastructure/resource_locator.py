"""打包资源定位（仅 app.py 消费；业务模块不得感知 frozen 状态）。

frozen（PyInstaller .app）布局约定：
- 可执行文件位于 Contents/MacOS/
- 捆绑二进制位于 Contents/MacOS/bin/（不依赖 _MEIPASS）
- 资源位于 Contents/Resources/
开发环境下全部返回 None（调用方回退既有行为）。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结环境。"""
    return bool(getattr(sys, "frozen", False))


def bundled_bin_dir() -> Path | None:
    """捆绑二进制目录：frozen 时为 Contents/MacOS/bin，否则 None。"""
    if not is_frozen():
        return None
    return Path(sys.executable).resolve().parent / "bin"


def bundled_resources_dir() -> Path | None:
    """资源目录：frozen 时为 Contents/Resources，否则 None。"""
    if not is_frozen():
        return None
    return Path(sys.executable).resolve().parent.parent / "Resources"


def bundled_file(name: str) -> Path | None:
    """按名取 Resources 内文件；不存在或非 frozen 时返回 None。"""
    resources = bundled_resources_dir()
    if resources is None:
        return None
    candidate = resources / name
    return candidate if candidate.exists() else None
