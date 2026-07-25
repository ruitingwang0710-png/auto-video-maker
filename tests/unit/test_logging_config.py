"""日志配置测试：轮转参数、防叠加、写入位置。"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from auto_video_maker.infrastructure.logging_config import (
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    default_log_dir,
    default_log_path,
    setup_logging,
)


def cleanup_handlers(log_file: Path) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, RotatingFileHandler) and \
                Path(handler.baseFilename) == log_file.resolve():
            root.removeHandler(handler)
            handler.close()


def test_default_paths_in_user_directory() -> None:
    assert str(default_log_dir()).startswith(str(Path.home()))
    assert default_log_path().name == "app.log"


def test_rotating_file_handler_configured(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "app.log"
    try:
        setup_logging(log_file=log_file)
        handlers = [
            handler for handler in logging.getLogger().handlers
            if isinstance(handler, RotatingFileHandler)
            and Path(handler.baseFilename) == log_file.resolve()
        ]
        assert len(handlers) == 1
        assert handlers[0].maxBytes == LOG_MAX_BYTES == 1_000_000
        assert handlers[0].backupCount == LOG_BACKUP_COUNT == 3
        logging.getLogger("test").info("轮转日志写入测试")
        handlers[0].flush()
        assert "轮转日志写入测试" in log_file.read_text(encoding="utf-8")
    finally:
        cleanup_handlers(log_file)


def test_repeat_setup_does_not_duplicate(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    try:
        setup_logging(log_file=log_file)
        setup_logging(log_file=log_file)
        setup_logging(log_file=log_file)
        matching = [
            handler for handler in logging.getLogger().handlers
            if isinstance(handler, RotatingFileHandler)
            and Path(handler.baseFilename) == log_file.resolve()
        ]
        assert len(matching) == 1
    finally:
        cleanup_handlers(log_file)
