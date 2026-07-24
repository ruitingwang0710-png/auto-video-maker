"""场景编辑界面。

只负责展示与交互；拆分、Scene 创建、保存均通过注入的 SceneService。
不包含拆分逻辑，不直接读写 JSON。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pathlib import Path

from auto_video_maker.infrastructure.task_runner import TaskRunner
from auto_video_maker.models.project import Project
from auto_video_maker.providers.image_provider import ImageProvider
from auto_video_maker.services.asset_download_service import (
    AssetDownloadError,
    AssetDownloadService,
)
from auto_video_maker.services.keyword_service import KeywordService
from auto_video_maker.services.scene_service import (
    SceneService,
    SceneServiceError,
    ScenesExistError,
)
from auto_video_maker.services.smart_split_service import (
    SmartSplitError,
    SmartSplitService,
)
from auto_video_maker.ui.image_search_dialog import ImageSearchDialog
from auto_video_maker.ui.scene_preview_dialog import PreviewChoice, ScenePreviewDialog

_PREVIEW_LENGTH = 24

PRIVACY_NOTICE = (
    "智能分镜会将当前文案发送至你配置的模型服务。\n"
    "请确认文案不包含不希望提交给第三方的信息。"
)


class ScenePage(QDialog):
    """场景列表的查看、拆分、编辑、增删、排序与保存。"""

    def __init__(
        self,
        project: Project,
        scene_service: SceneService,
        smart_split_service: SmartSplitService | None = None,
        task_runner: TaskRunner | None = None,
        image_provider: ImageProvider | None = None,
        download_service: AssetDownloadService | None = None,
        keyword_service: KeywordService | None = None,
        project_root: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self._scene_service = scene_service
        self._smart_split_service = smart_split_service
        self._task_runner = task_runner
        self._image_provider = image_provider
        self._download_service = download_service
        self._keyword_service = keyword_service
        self._project_root = project_root
        self._smart_task_id: int | None = None
        self._progress: QProgressDialog | None = None

        self.setWindowTitle(f"场景编辑 — {project.project_name}")
        self.setMinimumSize(720, 480)

        # 左侧：场景列表与列表操作
        self.scene_list = QListWidget(self)
        self.scene_list.currentRowChanged.connect(self._on_selection_changed)

        self.split_button = QPushButton("拆分文案", self)
        self.split_button.clicked.connect(self._on_split)
        self.smart_split_button = QPushButton("智能拆分", self)
        self.smart_split_button.clicked.connect(self._on_smart_split)
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
            self.smart_split_button,
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

        # 配图区域
        self.asset_status_label = QLabel("未配图", self)
        self.asset_status_label.setWordWrap(True)
        self.search_image_button = QPushButton("搜索图片", self)
        self.search_image_button.clicked.connect(self._on_search_image)
        self.local_image_button = QPushButton("使用本地图片", self)
        self.local_image_button.clicked.connect(self._on_local_image)

        asset_buttons = QHBoxLayout()
        asset_buttons.addWidget(self.search_image_button)
        asset_buttons.addWidget(self.local_image_button)
        asset_buttons.addStretch(1)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("场景文字", self))
        right_layout.addWidget(self.editor)
        right_layout.addLayout(editor_buttons)
        right_layout.addWidget(QLabel("场景配图", self))
        right_layout.addWidget(self.asset_status_label)
        right_layout.addLayout(asset_buttons)

        layout = QHBoxLayout(self)
        layout.addLayout(left_layout, 3)
        layout.addLayout(right_layout, 2)

        self._refresh_list()
        self._refresh_smart_split_button()

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

    # ------------------------------------------------------------ 智能拆分

    def _refresh_smart_split_button(self) -> None:
        """按可用性条件刷新智能拆分按钮状态（不满足则置灰并提示原因）。"""
        if self._smart_split_service is None or self._task_runner is None:
            self.smart_split_button.setEnabled(False)
            self.smart_split_button.setToolTip("智能分镜未启用。")
            return
        check = self._smart_split_service.availability()
        self.smart_split_button.setEnabled(check.available)
        self.smart_split_button.setToolTip(check.reason if not check.available else "")

    def _on_smart_split(self) -> None:
        if self._smart_split_service is None or self._task_runner is None:
            return
        check = self._smart_split_service.availability()
        if not check.available:
            QMessageBox.information(self, "智能拆分不可用", check.reason)
            self._refresh_smart_split_button()
            return
        # 隐私确认（与规范化 base_url 绑定；取消则零网络请求）
        if self._smart_split_service.needs_privacy_confirmation():
            answer = QMessageBox.question(
                self,
                "发送文案确认",
                PRIVACY_NOTICE,
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Ok:
                return
            self._smart_split_service.record_privacy_confirmation()
        self._start_llm_task()

    def _start_llm_task(self) -> None:
        assert self._smart_split_service is not None and self._task_runner is not None
        service = self._smart_split_service
        script = self._project.original_script
        self._set_split_buttons_enabled(False)
        progress = QProgressDialog("正在请求模型拆分…", "取消", 0, 0, self)
        progress.setWindowTitle("智能拆分")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        self._progress = progress
        task_id = self._task_runner.run(
            lambda: service.split_with_llm(script),
            self._on_smart_success,
            self._on_smart_error,
        )
        self._smart_task_id = task_id
        progress.canceled.connect(lambda: self._on_smart_cancel(task_id))
        progress.show()

    def _on_smart_cancel(self, task_id: int) -> None:
        """取消：使 task_id 失效并立即恢复 UI；晚到结果由 TaskRunner 丢弃。"""
        if self._task_runner is not None:
            self._task_runner.cancel(task_id)
        self._finish_llm_task()

    def _on_smart_success(self, texts: list[str]) -> None:
        self._finish_llm_task()
        self._show_preview(texts, show_use_rules=True)

    def _on_smart_error(self, exc: Exception) -> None:
        self._finish_llm_task()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("智能拆分失败")
        box.setText(str(exc))
        retry_button = box.addButton("重试", QMessageBox.ButtonRole.AcceptRole)
        rules_button = box.addButton("改用规则拆分", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is retry_button:
            self._on_smart_split()
        elif clicked is rules_button:
            self._run_rules_preview()

    def _finish_llm_task(self) -> None:
        self._smart_task_id = None
        if self._progress is not None:
            self._progress.blockSignals(True)  # 避免 close 触发 canceled
            self._progress.close()
            self._progress = None
        self._set_split_buttons_enabled(True)
        self._refresh_smart_split_button()

    def _set_split_buttons_enabled(self, enabled: bool) -> None:
        self.split_button.setEnabled(enabled)
        self.smart_split_button.setEnabled(enabled)

    def _run_rules_preview(self) -> None:
        if self._smart_split_service is None:
            return
        try:
            texts = self._smart_split_service.split_with_rules(
                self._project.original_script
            )
        except SmartSplitError as exc:
            QMessageBox.warning(self, "拆分失败", str(exc))
            return
        self._show_preview(texts, show_use_rules=False)

    def _show_preview(self, texts: list[str], show_use_rules: bool) -> None:
        dialog = ScenePreviewDialog(texts, show_use_rules=show_use_rules, parent=self)
        dialog.exec()
        if dialog.choice == PreviewChoice.APPLY:
            self._apply_confirmed_texts(texts)
        elif dialog.choice == PreviewChoice.USE_RULES:
            self._run_rules_preview()
        # CANCEL：不改动任何数据

    def _apply_confirmed_texts(self, texts: list[str]) -> None:
        try:
            self._scene_service.replace_from_texts(self._project, texts)
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
                self._scene_service.replace_from_texts(
                    self._project, texts, overwrite=True
                )
            except SceneServiceError as retry_exc:
                QMessageBox.warning(self, "无法应用拆分结果", str(retry_exc))
                return
        except SceneServiceError as exc:
            QMessageBox.warning(self, "无法应用拆分结果", str(exc))
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

    # ------------------------------------------------------------ 配图

    def _image_features_ready(self) -> bool:
        return all(
            component is not None
            for component in (
                self._image_provider,
                self._download_service,
                self._keyword_service,
                self._task_runner,
                self._project_root,
            )
        )

    def _on_search_image(self) -> None:
        row = self.scene_list.currentRow()
        if row < 0 or not self._image_features_ready():
            return
        scene = self._project.scenes[row]

        def privacy_gate() -> bool:
            if self._smart_split_service is None:
                return False
            if not self._smart_split_service.needs_privacy_confirmation():
                return True
            answer = QMessageBox.question(
                self,
                "发送文案确认",
                PRIVACY_NOTICE,
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Ok:
                return False
            self._smart_split_service.record_privacy_confirmation()
            return True

        dialog = ImageSearchDialog(
            scene_text=scene.text,
            initial_keywords=list(scene.search_keywords),
            project_root=self._project_root,
            image_provider=self._image_provider,
            download_service=self._download_service,
            keyword_service=self._keyword_service,
            task_runner=self._task_runner,
            privacy_gate=privacy_gate,
            parent=self,
        )
        dialog.exec()
        try:
            if dialog.edited_keywords and dialog.edited_keywords != scene.search_keywords:
                self._scene_service.set_scene_keywords(
                    self._project, row, dialog.edited_keywords
                )
            if dialog.selected_asset is not None:
                self._scene_service.set_scene_asset(
                    self._project, row, dialog.selected_asset
                )
        except SceneServiceError as exc:
            QMessageBox.warning(self, "无法保存配图", str(exc))
        self._refresh_asset_status(row)

    def _on_local_image(self) -> None:
        row = self.scene_list.currentRow()
        if row < 0 or self._download_service is None or self._project_root is None:
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择本地图片", "", "图片文件 (*.jpg *.jpeg *.png *.webp)"
        )
        if not file_path:
            return
        try:
            asset = self._download_service.import_local_file(
                Path(file_path), self._project_root
            )
            self._scene_service.set_scene_asset(self._project, row, asset)
        except (AssetDownloadError, SceneServiceError) as exc:
            QMessageBox.warning(self, "无法使用本地图片", str(exc))
            return
        self._refresh_asset_status(row)

    def _refresh_asset_status(self, row: int) -> None:
        has_selection = 0 <= row < len(self._project.scenes)
        features = self._image_features_ready()
        self.search_image_button.setEnabled(has_selection and features)
        self.local_image_button.setEnabled(
            has_selection
            and self._download_service is not None
            and self._project_root is not None
        )
        if not has_selection:
            self.asset_status_label.setText("未配图")
            return
        asset = self._project.scenes[row].selected_asset
        if not asset:
            self.asset_status_label.setText("未配图")
        else:
            author = asset.get("author") or "未知作者"
            license_name = str(asset.get("license", "")).upper() or "未知许可"
            self.asset_status_label.setText(
                f"已配图：{asset.get('local_path', '')}\n"
                f"作者：{author}    许可证：{license_name}"
            )

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
        self._refresh_asset_status(row)

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
