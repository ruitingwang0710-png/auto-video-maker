"""应用入口。

本地启动：
    python -m auto_video_maker.app
或安装后：
    auto-video-maker
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from auto_video_maker.infrastructure.logging_config import setup_logging
from auto_video_maker.ui.main_window import APP_NAME, MainWindow

logger = logging.getLogger(__name__)


def main() -> int:
    """启动桌面应用，返回退出码。"""
    setup_logging()
    logger.info("应用启动: %s", APP_NAME)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    exit_code = app.exec()
    logger.info("应用退出，退出码 %s", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
