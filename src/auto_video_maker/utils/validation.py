"""输入校验工具：项目名称与目录可写性检查。

规则（见 TASK.md「已确认默认规则」）：
- 中文项目名称允许使用
- 项目名称禁止 /、\\、..、控制字符及 Windows 保留名称
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

_FORBIDDEN_CHARS = ("/", "\\")


class ValidationError(ValueError):
    """输入校验失败。消息面向普通用户，可直接展示。"""


def validate_project_name(name: str) -> str:
    """校验项目名称，返回去除首尾空白后的名称。

    校验失败时抛出 ValidationError。
    """
    cleaned = name.strip()
    if not cleaned:
        raise ValidationError("项目名称不能为空。")
    for char in _FORBIDDEN_CHARS:
        if char in cleaned:
            raise ValidationError(f"项目名称不能包含字符 {char!r}。")
    if ".." in cleaned:
        raise ValidationError("项目名称不能包含 '..'。")
    if any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise ValidationError("项目名称不能包含控制字符。")
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValidationError(f"项目名称不能使用系统保留名称 {cleaned!r}。")
    if cleaned in {".", "~"}:
        raise ValidationError(f"项目名称不能为 {cleaned!r}。")
    return cleaned


def validate_script(script: str) -> str:
    """校验文案非空，返回去除首尾空白后的文案。"""
    cleaned = script.strip()
    if not cleaned:
        raise ValidationError("文案不能为空。")
    return cleaned


def ensure_writable_directory(directory: Path) -> Path:
    """确认目录存在且可写，返回解析后的绝对路径。

    目录不存在时尝试创建；不可写或无法创建时抛出 ValidationError。
    使用真实写入探测，而不是仅依赖 os.access。
    """
    resolved = directory.expanduser().resolve()
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValidationError(f"无法创建输出目录：{resolved}（{exc.strerror or exc}）。") from exc
    if not resolved.is_dir():
        raise ValidationError(f"输出目录不可用：{resolved} 不是目录。")
    try:
        with tempfile.NamedTemporaryFile(dir=resolved, prefix=".avm_write_test_", delete=False) as probe:
            probe_path = Path(probe.name)
        probe_path.unlink(missing_ok=True)
    except OSError as exc:
        raise ValidationError(
            f"输出目录不可写：{resolved}。请选择其他目录或检查目录权限。"
        ) from exc
    return resolved


def ensure_within_directory(child: Path, parent: Path) -> None:
    """确认 child 位于 parent 内部，防止路径逃逸。"""
    child_resolved = child.expanduser().resolve()
    parent_resolved = parent.expanduser().resolve()
    if not child_resolved.is_relative_to(parent_resolved):
        raise ValidationError("项目名称不能写入输出目录之外的位置。")
    if os.path.normpath(str(child_resolved)) == os.path.normpath(str(parent_resolved)):
        raise ValidationError("项目目录不能与输出目录相同。")
