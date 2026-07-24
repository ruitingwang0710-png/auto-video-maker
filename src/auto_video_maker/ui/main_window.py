"""应用首页。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from auto_video_maker.infrastructure.config import ConfigStore
from auto_video_maker.infrastructure.secret_store import SecretStore
from auto_video_maker.infrastructure.task_runner import TaskRunner
from auto_video_maker.models.project import Project
from auto_video_maker.providers.image_provider import ImageProvider
from auto_video_maker.services.asset_download_service import AssetDownloadService
from auto_video_maker.services.keyword_service import KeywordService
from auto_video_maker.services.project_manager import (
    PROJECT_FILE_NAME,
    ProjectManager,
    ProjectManagerError,
)
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.smart_split_service import SmartSplitService
from auto_video_maker.ui.new_project_dialog import NewProjectDialog
from auto_video_maker.ui.scene_page import ScenePage
from auto_video_maker.ui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)

APP_NAME = "Auto Video Maker"


class MainWindow(QMainWindow):
    """首页：应用名称、新建项目、打开项目、最近项目、设置。

    依赖由 app.py（composition root）注入。
    """

    def __init__(
        self,
        project_manager: ProjectManager,
        scene_service: SceneService,
        smart_split_service: SmartSplitService | None = None,
        config_store: ConfigStore | None = None,
        secret_store: SecretStore | None = None,
        task_runner: TaskRunner | None = None,
        image_provider: ImageProvider | None = None,
        download_service: AssetDownloadService | None = None,
        keyword_service: KeywordService | None = None,
    ) -> None:
        super().__init__()
        self._project_manager = project_manager
        self._scene_service = scene_service
        self._smart_split_service = smart_split_service
        self._config_store = config_store
        self._secret_store = secret_store
        self._task_runner = task_runner
        self._image_provider = image_provider
        self._download_service = download_service
        self._keyword_service = keyword_service

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(520, 480)

        title_label = QLabel(APP_NAME, self)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; margin: 12px;")

        self.new_project_button = QPushButton("新建项目", self)
        self.new_project_button.clicked.connect(self._on_new_project)

        self.open_project_button = QPushButton("打开项目", self)
        self.open_project_button.clicked.connect(self._on_open_project)

        self.settings_button = QPushButton("设置", self)
        self.settings_button.clicked.connect(self._on_settings)

        recent_label = QLabel("最近项目", self)
        self.recent_list = QListWidget(self)
        self.recent_empty_label = QLabel("暂无最近项目", self)
        self.recent_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_empty_label.setStyleSheet("color: gray; margin: 8px;")
        self.recent_list.hide()

        layout = QVBoxLayout()
        layout.addWidget(title_label)
        layout.addWidget(self.new_project_button)
        layout.addWidget(self.open_project_button)
        layout.addWidget(self.settings_button)
        layout.addWidget(recent_label)
        layout.addWidget(self.recent_empty_label)
        layout.addWidget(self.recent_list)
        layout.addStretch(1)

        container = QWidget(self)
        container.setLayout(layout)
        self.setCentralWidget(container)

    # ------------------------------------------------------------------ 槽函数

    def _on_new_project(self) -> None:
        dialog = NewProjectDialog(self._project_manager, parent=self)
        if dialog.exec() and dialog.created_project is not None:
            project = dialog.created_project
            self._add_recent(project)
            self._open_scene_page(project)

    def _on_open_project(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开项目",
            "",
            f"项目文件 ({PROJECT_FILE_NAME})",
        )
        if not file_path:
            return
        try:
            project = self._project_manager.load_project(Path(file_path))
        except ProjectManagerError as exc:
            QMessageBox.warning(self, "无法打开项目", str(exc))
            return
        self._add_recent(project)
        self._open_scene_page(project)

    def _open_scene_page(self, project: Project) -> None:
        """进入场景编辑页面（模态；关闭时由页面负责未保存保护）。"""
        page = ScenePage(
            project,
            self._scene_service,
            smart_split_service=self._smart_split_service,
            task_runner=self._task_runner,
            image_provider=self._image_provider,
            download_service=self._download_service,
            keyword_service=self._keyword_service,
            project_root=self._project_manager.project_directory(project),
            parent=self,
        )
        page.exec()

    def _on_settings(self) -> None:
        if self._config_store is None or self._secret_store is None:
            QMessageBox.information(self, "设置", "设置功能当前不可用。")
            return
        dialog = SettingsDialog(self._config_store, self._secret_store, parent=self)
        dialog.exec()

    # ------------------------------------------------------------------ 辅助

    def _add_recent(self, project: Project) -> None:
        """将项目加入最近项目列表（Phase 1 仅当前会话内有效）。"""
        project_dir = self._project_manager.project_directory(project)
        item = QListWidgetItem(f"{project.project_name} — {project_dir}")
        self.recent_list.insertItem(0, item)
        self.recent_empty_label.hide()
        self.recent_list.show()
