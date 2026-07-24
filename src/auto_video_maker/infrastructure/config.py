"""应用配置：config.json 读写与 base_url 规范化。

config.json 只保存普通配置，绝不包含 API Key。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

APP_CONFIG_DIR_NAME = "AutoVideoMaker"
CONFIG_FILE_NAME = "config.json"

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2


def default_config_path() -> Path:
    """返回平台默认的配置文件路径。"""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_CONFIG_DIR_NAME / CONFIG_FILE_NAME


def normalize_base_url(base_url: str) -> str:
    """规范化 base_url：去首尾空白、小写 scheme 与主机、去末尾斜杠。

    不做合法性校验（校验在 LLMClient 中进行）。
    """
    cleaned = base_url.strip()
    if not cleaned:
        return ""
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return cleaned.rstrip("/")
    if not parts.scheme or not parts.netloc:
        return cleaned.rstrip("/")
    netloc = parts.netloc
    host_port = netloc.rsplit("@", 1)[-1]  # 保留但不小写用户信息以便后续校验拒绝
    if "@" in netloc:
        userinfo = netloc.rsplit("@", 1)[0]
        netloc = userinfo + "@" + host_port.lower()
    else:
        netloc = host_port.lower()
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


@dataclass
class LLMSettings:
    """LLM 智能分镜配置（不含 API Key）。"""

    enabled: bool = False
    base_url: str = ""
    model: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    privacy_confirmed_for_base_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "privacy_confirmed_for_base_url": self.privacy_confirmed_for_base_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMSettings":
        defaults = cls()
        def _bool(key: str) -> bool:
            value = data.get(key, getattr(defaults, key))
            return bool(value) if isinstance(value, bool) else getattr(defaults, key)

        def _str(key: str) -> str:
            value = data.get(key, getattr(defaults, key))
            return value if isinstance(value, str) else getattr(defaults, key)

        def _number(key: str, minimum: float) -> float:
            value = data.get(key, getattr(defaults, key))
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= minimum:
                return float(value)
            return float(getattr(defaults, key))

        return cls(
            enabled=_bool("enabled"),
            base_url=_str("base_url"),
            model=_str("model"),
            timeout_seconds=_number("timeout_seconds", 1.0),
            max_retries=int(_number("max_retries", 0)),
            privacy_confirmed_for_base_url=_str("privacy_confirmed_for_base_url"),
        )


class ConfigStore:
    """config.json 的读写：原子写入，创建后权限 600，容错回落默认值。"""

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = config_path or default_config_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> LLMSettings:
        """读取配置；文件缺失、损坏或字段非法时回落默认值。"""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return LLMSettings()
        except OSError as exc:
            logger.warning("读取配置失败，使用默认配置：%s", exc)
            return LLMSettings()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("配置文件损坏，使用默认配置：%s", self._path)
            return LLMSettings()
        if not isinstance(data, dict):
            logger.warning("配置文件格式无效，使用默认配置：%s", self._path)
            return LLMSettings()
        return LLMSettings.from_dict(data)

    def save(self, settings: LLMSettings) -> None:
        """原子写入配置并将权限设为 600。绝不写入 API Key。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n"
        fd, temp_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".config_", suffix=".tmp"
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self._path)
        except OSError:
            temp_path.unlink(missing_ok=True)
            raise
        logger.info("配置已保存：%s", self._path)
