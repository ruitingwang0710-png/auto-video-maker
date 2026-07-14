"""场景编辑界面。

只负责展示与交互；拆分、Scene 创建、保存均通过注入的 SceneService。
不包含拆分逻辑，不直接读写 JSON。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from auto_video_maker.models.project import Project
from auto_video_maker.services.scene_service import (
    SceneService,
    SceneServiceError,
    ScenesExistError,
)

_PREVIEW_LENGTH = 24


class ScenePage(QDialog):
    """场景列表的查看、拆分、编辑、增删、排序与保存。"""

    def __init__(
        self,
        project: Project,
        scene_service: SceneService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self._scene_service = scene_service

        self.setWindowTitle(f"场景编辑 — {project.project_name}")
        self.setMinimumSize(720, 480)

        # 左侧：场景列表与列表操作
        self.scene_list = QListWidget(self)
        self.scene_list.currentRowChanged.connect(self._on_selection_changed)

        self.split_button = QPushButton("拆分文案", self)
        self.split_button.clicked.connect(self._on_split)
        self.add_button = QPushButton("新增场景", self)
        self.add_button.clicked.connect(self._on_add)
        self.delete_button = QPushButton("删除场景", self)
        self.delete_button.clicked.connect(self._on_delete)
        self.move_up_button = QPushButton("上移", self)
        self.move_up_button.clicked.connect(self._on_move_up)
        self.move_down_button = QPushButton("下移", self)
        self.move_down_button.clicked.connect(self._on_move_down)

        list_buttons = QHBoxLayout()
        for button in (
            self.split_button,
            self.add_button,
            self.delete_button,
            self.move_up_button,
            self.move_down_button,
        ):
            list_buttons.addWidget(button)

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("场景列表", self))
        left_layout.addWidget(self.scene_list)
        left_layout.addLayout(list_buttons)

        # 右侧：场景文字编辑
        self.editor = QPlainTextEdit(self)
        self.editor.setPlaceholderText("选择左侧场景后在此编辑文字")
        self.apply_button = QPushButton("应用修改", self)
        self.apply_button.clicked.connect(self._on_apply_edit)
        self.save_button = QPushButton("保存项目", self)
        self.save_button.clicked.connect(self._on_save)

        editor_buttons = QHBoxLayout()
        editor_buttons.addWidget(self.apply_button)
        editor_buttons.addStretch(1)
        editor_buttons.addWidget(self.save_button)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("场景文字", self))
        right_layout.addWidget(self.editor)
        right_layout.addLayout(editor_buttons)

        layout = QHBoxLayout(self)
        layout.addLayout(left_layout, 3)
        layout.addLayout(right_layout, 2)

        self._refresh_list()

    # ------------------------------------------------------------ 槽函数

    def _on_split(self) -> None:
        try:
            self._scene_service.split_script(self._project)
        except ScenesExistError as exc:
            answer = QMessageBox.question(
                self,
                "覆盖现有场景？",
                f"{exc}\n\n是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self._scene_service.split_script(self._project, overwrite=True)
            except SceneServiceError as retry_exc:
                QMessageBox.warning(self, "拆分失败", str(retry_exc))
                return
        except SceneServiceError as exc:
            QMessageBox.warning(self, "拆分失败", str(exc))
            return
        self._refresh_list(select_row=0)

    def _on_apply_edit(self) -> None:
        row = self.scene_list.currentRow()
        if row < 0:
            return
        try:
            self._scene_service.update_scene_text(self._project, row, self.editor.toPlainText())
        except SceneServiceError as exc:
            QMessageBox.warning(self, "无法修改场景", str(exc))
            return
        self._refresh_list(select_row=row)

    def _on_add(self) -> None:
        self._scene_service.add_scene(self._project)
        self._refresh_list(select_row=len(self._project.scenes) - 1)

    def _on_delete(self) -> None:
        row = self.scene_list.currentRow()
        if row < 0:
            return
        try:
            self._scene_service.delete_scene(self._project, row)
        except SceneServiceError as exc:
            QMessageBox.warning(self, "无法删除场景", str(exc))
            return
        self._refresh_list(select_row=min(row, len(self._project.scenes) - 1))

    def _on_move_up(self) -> None:
        row = self.scene_list.currentRow()
        if row < 0:
            return
        new_row = self._scene_service.move_scene_up(self._project, row)
        self._refresh_list(select_row=new_row)

    def _on_move_down(self) -> None:
        row = self.scene_list.currentRow()
        if row < 0:
            return
        new_row = self._scene_service.move_scene_down(self._project, row)
        self._refresh_list(select_row=new_row)

    def _on_save(self) -> bool:
        try:
            self._scene_service.save(self._project)
        except SceneServiceError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False
        return True

    # ------------------------------------------------------------ 关闭保护

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt 命名)
        if not self._scene_service.is_dirty:
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "有未保存的修改",
            "场景有未保存的修改。要保存吗？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            if self._on_save():
                event.accept()
            else:
                event.ignore()
        elif answer == QMessageBox.StandardButton.Discard:
            self._scene_service.discard_changes()
            event.accept()
        else:
            event.ignore()

    def reject(self) -> None:  # Esc 键也走未保存保护
        self.close()

    # ------------------------------------------------------------ 辅助

    def _on_selection_changed(self, row: int) -> None:
        has_selection = 0 <= row < len(self._project.scenes)
        if has_selection:
            self.editor.setPlainText(self._project.scenes[row].text)
        else:
            self.editor.clear()
        self.delete_button.setEnabled(has_selection)
        self.apply_button.setEnabled(has_selection)
        self.move_up_button.setEnabled(has_selection and row > 0)
        self.move_down_button.setEnabled(
            has_selection and row < len(self._project.scenes) - 1
        )

    def _refresh_list(self, select_row: int | None = None) -> None:
        self.scene_list.blockSignals(True)
        self.scene_list.clear()
        for scene in self._project.scenes:
            preview = " ".join(scene.text.split()) or "（空场景）"
            if len(preview) > _PREVIEW_LENGTH:
                preview = preview[:_PREVIEW_LENGTH] + "…"
            self.scene_list.addItem(f"{scene.scene_id:>2}. {preview}")
        self.scene_list.blockSignals(False)
        if select_row is not None and 0 <= select_row < self.scene_list.count():
            self.scene_list.setCurrentRow(select_row)
        else:
            self._on_selection_changed(self.scene_list.currentRow())
