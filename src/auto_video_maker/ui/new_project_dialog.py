"""新建项目窗口。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from auto_video_maker.models.project import ASPECT_RATIO_RESOLUTIONS, Project
from auto_video_maker.services.project_manager import ProjectManager, ProjectManagerError


class NewProjectDialog(QDialog):
    """收集项目名称、文案、视频比例与输出目录，调用 ProjectManager 创建项目。"""

    def __init__(self, project_manager: ProjectManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_manager = project_manager
        self.created_project: Project | None = None

        self.setWindowTitle("新建项目")
        self.setMinimumWidth(480)

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("例如：我的第一个视频")

        self.script_edit = QPlainTextEdit(self)
        self.script_edit.setPlaceholderText("在这里粘贴中文文案……")
        self.script_edit.setMinimumHeight(140)

        self.aspect_combo = QComboBox(self)
        for ratio, resolution in ASPECT_RATIO_RESOLUTIONS.items():
            self.aspect_combo.addItem(f"{ratio}（{resolution}）", userData=ratio)

        # 语音与语速：UI 显示文字 → 模型稳定内部值
        self.voice_combo = QComboBox(self)
        self.voice_combo.addItem("女声", userData="female")
        self.voice_combo.addItem("男声", userData="male")

        self.rate_combo = QComboBox(self)
        self.rate_combo.addItem("慢", userData="-20%")
        self.rate_combo.addItem("正常", userData="+0%")
        self.rate_combo.addItem("快", userData="+20%")
        self.rate_combo.setCurrentIndex(1)

        self.output_dir_edit = QLineEdit(self)
        self.output_dir_edit.setPlaceholderText("选择项目保存位置")
        browse_button = QPushButton("浏览…", self)
        browse_button.clicked.connect(self._choose_output_directory)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_dir_edit)
        output_row.addWidget(browse_button)

        form = QFormLayout(self)
        form.addRow("项目名称", self.name_edit)
        form.addRow("文案", self.script_edit)
        form.addRow("视频比例", self.aspect_combo)
        form.addRow("语音", self.voice_combo)
        form.addRow("语速", self.rate_combo)
        form.addRow("输出目录", output_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("创建项目")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _choose_output_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if directory:
            self.output_dir_edit.setText(directory)

    def _on_accept(self) -> None:
        try:
            self.created_project = self._project_manager.create_project(
                project_name=self.name_edit.text(),
                original_script=self.script_edit.toPlainText(),
                aspect_ratio=self.aspect_combo.currentData(),
                output_directory=self.output_dir_edit.text(),
                voice=self.voice_combo.currentData(),
                speech_rate=self.rate_combo.currentData(),
            )
        except ProjectManagerError as exc:
            QMessageBox.warning(self, "无法创建项目", str(exc))
            return
        self.accept()
