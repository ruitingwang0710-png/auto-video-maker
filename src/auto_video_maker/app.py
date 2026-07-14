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
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.scene_splitter import RuleBasedSceneSplitter
from auto_video_maker.ui.main_window import APP_NAME, MainWindow

logger = logging.getLogger(__name__)


def main() -> int:
    """启动桌面应用，返回退出码。

    本函数是唯一的 composition root：
    RuleBasedSceneSplitter → SceneService → MainWindow / ScenePage。
    """
    setup_logging()
    logger.info("应用启动: %s", APP_NAME)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    project_manager = ProjectManager()
    splitter = RuleBasedSceneSplitter()
    scene_service = SceneService(splitter, project_manager)
    window = MainWindow(project_manager, scene_service)
    window.show()
    exit_code = app.exec()
    logger.info("应用退出，退出码 %s", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
