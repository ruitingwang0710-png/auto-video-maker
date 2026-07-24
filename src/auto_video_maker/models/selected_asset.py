"""SelectedAsset：场景选中素材的唯一验证/转换入口。

- 写入路径（from_dict / 构造）严格校验，失败抛出 AssetValidationError
- 读取路径（from_storage）容错补默认值，绝不因旧数据缺字段而拒绝加载
- local_path 必须是相对项目根目录的路径，禁止绝对路径与 `..` 逃逸
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath
from typing import Any

LOCAL_PROVIDER = "local"
USER_PROVIDED_LICENSE = "user-provided"

_STRING_FIELDS = (
    "provider",
    "source",
    "asset_id",
    "title",
    "local_path",
    "source_page",
    "author",
    "author_url",
    "license",
    "license_version",
    "license_url",
    "attribution",
)

_REQUIRED_NON_EMPTY = ("provider", "asset_id", "local_path", "license")


class AssetValidationError(ValueError):
    """素材数据无效。消息面向用户，可直接展示。"""


def _validate_relative_path(local_path: str) -> None:
    """校验 local_path 为不含逃逸的相对路径（不接触文件系统）。"""
    if not local_path.strip():
        raise AssetValidationError("素材路径不能为空。")
    if "\\" in local_path:
        raise AssetValidationError("素材路径不能包含反斜杠。")
    pure = PurePosixPath(local_path)
    if pure.is_absolute() or Path(local_path).is_absolute():
        raise AssetValidationError("素材路径必须是相对于项目目录的路径。")
    if any(part == ".." for part in pure.parts):
        raise AssetValidationError("素材路径不能包含 '..'。")


@dataclass
class SelectedAsset:
    """选中素材的完整版权元数据（见 TASK.md 字段表）。"""

    provider: str
    source: str
    asset_id: str
    title: str
    local_path: str
    source_page: str
    author: str
    author_url: str
    license: str
    license_version: str
    license_url: str
    attribution: str
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        for name in _STRING_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str):
                raise AssetValidationError(f"素材字段 {name} 必须是文本。")
        for name in _REQUIRED_NON_EMPTY:
            if not getattr(self, name).strip():
                raise AssetValidationError(f"素材字段 {name} 不能为空。")
        for name in ("width", "height"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise AssetValidationError(f"素材字段 {name} 必须是整数或空。")
        _validate_relative_path(self.local_path)

    # ------------------------------------------------------------ 路径安全

    def resolve_within(self, project_root: Path) -> Path:
        """将 local_path 解析为 project_root 内的规范路径。

        解析结果位于项目目录外（含符号链接逃逸）时抛出 AssetValidationError。
        """
        root = project_root.resolve()
        resolved = (root / self.local_path).resolve()
        if not resolved.is_relative_to(root):
            raise AssetValidationError("素材路径指向项目目录之外，已拒绝。")
        return resolved

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source": self.source,
            "asset_id": self.asset_id,
            "title": self.title,
            "local_path": self.local_path,
            "source_page": self.source_page,
            "author": self.author,
            "author_url": self.author_url,
            "license": self.license,
            "license_version": self.license_version,
            "license_url": self.license_url,
            "attribution": self.attribution,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SelectedAsset":
        """严格解析（写入路径用）。缺字段或非法值抛出 AssetValidationError。"""
        if not isinstance(data, dict):
            raise AssetValidationError("素材数据必须是对象。")
        known = {field.name for field in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known}
        missing = [name for name in _STRING_FIELDS if name not in kwargs]
        if missing:
            raise AssetValidationError(f"素材数据缺少字段: {', '.join(missing)}。")
        return cls(**kwargs)

    @classmethod
    def from_storage(cls, data: Any) -> dict[str, Any] | None:
        """容错读取（加载 project.json 用）：补默认值、保持可打开。

        - None / 非对象 → None（旧项目 selected_asset=null 正常加载）
        - 合法对象 → 规范化后的字典（缺失字符串字段补 ""，尺寸非法置 None）
        - 数据不可修复（缺 provider/asset_id 等核心信息）时原样返回，
          不丢数据、不抛异常，由后续写入路径严格把关
        """
        if not isinstance(data, dict):
            return None
        normalized: dict[str, Any] = dict(data)
        for name in _STRING_FIELDS:
            value = normalized.get(name)
            normalized[name] = value if isinstance(value, str) else ""
        for name in ("width", "height"):
            value = normalized.get(name)
            if isinstance(value, bool) or not isinstance(value, int):
                normalized[name] = None
        try:
            return cls.from_dict(normalized).to_dict()
        except AssetValidationError:
            # 不可修复的旧数据：原样保留，保证项目仍能打开
            return dict(data)
