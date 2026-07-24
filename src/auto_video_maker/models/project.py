"""项目数据模型：Project / ProjectSettings / Scene。

字段定义以 ARCHITECTURE.md 与 TASK.md 为准。
时间统一使用 ISO 8601 字符串保存。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_VERSION = "0.1"

# 语速仅允许三档（TASK.md Phase 4 决议）
VALID_SPEECH_RATES = ("-20%", "+0%", "+20%")
DEFAULT_SPEECH_RATE = "+0%"

ASPECT_RATIO_RESOLUTIONS: dict[str, str] = {
    "9:16": "1080x1920",
    "16:9": "1920x1080",
}


def now_iso() -> str:
    """返回当前本地时间的 ISO 8601 字符串（含时区，精确到秒）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Scene:
    """单个场景（分镜）。

    图片来源、作者、许可等信息统一放在 selected_asset 字典中。
    """

    scene_id: int
    text: str
    search_keywords: list[str] = field(default_factory=list)
    selected_asset: dict[str, Any] | None = None
    audio_path: str | None = None
    duration: float | None = None
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "text": self.text,
            "search_keywords": list(self.search_keywords),
            "selected_asset": self.selected_asset,
            "audio_path": self.audio_path,
            "duration": self.duration,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scene":
        # 统一经 SelectedAsset 转换入口（容错）：null 与旧项目必须正常加载
        from auto_video_maker.models.selected_asset import SelectedAsset

        return cls(
            scene_id=int(data["scene_id"]),
            text=str(data["text"]),
            search_keywords=list(data.get("search_keywords") or []),
            selected_asset=SelectedAsset.from_storage(data.get("selected_asset")),
            audio_path=data.get("audio_path"),
            duration=data.get("duration"),
            status=str(data.get("status", "pending")),
        )


def _sanitize_speech_rate(value: Any) -> str:
    """语速容错：缺失/非法值安全回落默认档，不得原样传给 Provider。"""
    if isinstance(value, str) and value in VALID_SPEECH_RATES:
        return value
    if value is not None and value != DEFAULT_SPEECH_RATE:
        logger.warning("非法语速值已回落为默认档: %r", value)
    return DEFAULT_SPEECH_RATE


@dataclass
class ProjectSettings:
    """项目级设置。

    voice 只保存稳定内部值（"female" / "male"；旧项目 "default" 兼容为
    female，映射在 TTSProvider 内进行）。speech_rate 仅三档合法。
    """

    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    voice: str = "default"
    subtitle_enabled: bool = True
    output_directory: str = ""
    speech_rate: str = DEFAULT_SPEECH_RATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "voice": self.voice,
            "subtitle_enabled": self.subtitle_enabled,
            "output_directory": self.output_directory,
            "speech_rate": self.speech_rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectSettings":
        return cls(
            aspect_ratio=str(data.get("aspect_ratio", "9:16")),
            resolution=str(data.get("resolution", "1080x1920")),
            voice=str(data.get("voice", "default")),
            subtitle_enabled=bool(data.get("subtitle_enabled", True)),
            output_directory=str(data.get("output_directory", "")),
            speech_rate=_sanitize_speech_rate(data.get("speech_rate")),
        )


def _default_output() -> dict[str, Any]:
    return {"video_path": None, "subtitle_path": None, "status": "draft"}


@dataclass
class Project:
    """一个视频项目，可序列化为 project.json。"""

    project_name: str
    original_script: str
    settings: ProjectSettings
    project_version: str = PROJECT_VERSION
    project_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    scenes: list[Scene] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=_default_output)

    REQUIRED_KEYS = (
        "project_version",
        "project_id",
        "project_name",
        "created_at",
        "updated_at",
        "original_script",
        "settings",
        "scenes",
        "output",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_version": self.project_version,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "original_script": self.original_script,
            "settings": self.settings.to_dict(),
            "scenes": [scene.to_dict() for scene in self.scenes],
            "output": dict(self.output),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        missing = [key for key in cls.REQUIRED_KEYS if key not in data]
        if missing:
            raise ValueError(f"project.json 缺少必要字段: {', '.join(missing)}")
        return cls(
            project_name=str(data["project_name"]),
            original_script=str(data["original_script"]),
            settings=ProjectSettings.from_dict(data["settings"]),
            project_version=str(data["project_version"]),
            project_id=str(data["project_id"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            scenes=[Scene.from_dict(item) for item in data["scenes"]],
            output=dict(data["output"]),
        )
