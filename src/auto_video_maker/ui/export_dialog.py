"""导出对话框：进度、取消与完成操作。

只负责展示与交互；校验与渲染全部经 VideoRenderService，
命令与路径处理在 FFmpegRunner。UI 不构造命令、不改 Project。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from auto_video_maker.infrastructure.ffmpeg_runner import CancelToken
from auto_video_maker.infrastructure.task_runner import TaskRunner
from auto_video_maker.models.project import Project
from auto_video_maker.services.video_render_service import VideoRenderService

logger = logging.getLogger(__name__)


class ExportDialog(QDialog):
    """执行导出并展示进度。"""

    def __init__(
        self,
        project: Project,
        project_root: Path,
        render_service: VideoRenderService,
        task_runner: TaskRunner,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self._project_root = project_root
        self._render_service = render_service
        self._task_runner = task_runner
        self._cancel_token = CancelToken()
        self._task_id: int | None = None
        self._finished = False

        self.setWindowTitle("导出视频")
        self.setMinimumWidth(480)

        self.info_label = QLabel(
            f"输出位置：{project_root / 'output' / 'final_video.mp4'}", self
        )
        self.info_label.setWordWrap(True)
        self.step_label = QLabel("正在准备导出…", self)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.clicked.connect(self._on_cancel)
        self.open_button = QPushButton("打开输出文件夹", self)
        self.open_button.hide()
        self.open_button.clicked.connect(self._on_open_folder)
        self.close_button = QPushButton("关闭", self)
        self.close_button.hide()
        self.close_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.info_label)
        layout.addWidget(self.step_label)
        layout.addWidget(self.progress_bar)
        layout.addLayout(buttons)

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """提交后台导出任务。"""
        service = self._render_service
        project = self._project
        token = self._cancel_token
        self._task_id = self._task_runner.run(
            lambda report: service.render(
                project, on_progress=report, cancel_token=token
            ),
            self._on_success,
            self._on_error,
            on_progress=self._on_progress,
        )
        self.step_label.setText("正在渲染场景…")

    def _on_progress(self, percent: int) -> None:
        self.progress_bar.setValue(percent)
        if percent < 85:
            self.step_label.setText("正在渲染场景…")
        elif percent < 94:
            self.step_label.setText("正在合并与写入输出…")
        else:
            self.step_label.setText("正在完成导出…")

    def _on_success(self, relative_path: str) -> None:
        self._finished = True
        self.progress_bar.setValue(100)
        self.step_label.setText(f"导出完成：{relative_path}")
        self.cancel_button.hide()
        self.open_button.show()
        self.close_button.show()

    def _on_error(self, exc: Exception) -> None:
        self._finished = True
        self.step_label.setText("导出失败。")
        self.cancel_button.hide()
        self.close_button.show()
        QMessageBox.warning(self, "导出失败", str(exc))

    def _on_cancel(self) -> None:
        self._cancel_token.cancel()
        if self._task_id is not None:
            self._task_runner.cancel(self._task_id)
        self._finished = True
        self.step_label.setText("已取消。")
        self.cancel_button.hide()
        self.close_button.show()

    def _on_open_folder(self) -> None:
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self._project_root / "output"))
        )

    def reject(self) -> None:  # Esc / 关闭按钮
        if not self._finished:
            self._on_cancel()
        super().reject()
