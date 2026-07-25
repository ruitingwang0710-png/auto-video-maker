"""应用日志配置：控制台 + 用户目录轮转文件日志。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

LOG_MAX_BYTES = 1_000_000  # 1MB
LOG_BACKUP_COUNT = 3
LOG_FILE_NAME = "app.log"
APP_LOG_DIR_NAME = "AutoVideoMaker"


def default_log_dir() -> Path:
    """平台默认日志目录（用户目录，绝不在应用包内）。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_LOG_DIR_NAME
    return Path.home() / ".local" / "state" / APP_LOG_DIR_NAME / "logs"


def default_log_path() -> Path:
    return default_log_dir() / LOG_FILE_NAME


def setup_logging(level: int = logging.INFO, log_file: Path | None = None) -> logging.Logger:
    """配置根日志器：控制台 + 可选轮转文件（1MB × 3）。

    重复调用不会叠加处理器；文件日志沿用既有脱敏规则
    （各模块已保证不记录 API Key 及衍生、不记录完整文案）。
    """
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT)

    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
               for handler in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    if log_file is not None:
        log_file = Path(log_file).expanduser().resolve()
        already = any(
            isinstance(handler, RotatingFileHandler)
            and Path(getattr(handler, "baseFilename", "")) == log_file
            for handler in root.handlers
        )
        if not already:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)

    return root
