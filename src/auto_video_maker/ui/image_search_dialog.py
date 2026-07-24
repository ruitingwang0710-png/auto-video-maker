"""图片搜索对话框。

只负责展示与交互：搜索经 ImageProvider、下载经 AssetDownloadService、
后台执行经 TaskRunner。不创建 Scene、不读写 JSON、不拼装资产字典。
用户最终选择的 SelectedAsset 存于 self.selected_asset，
由调用方（场景页）经 SceneService 写入场景。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from auto_video_maker.infrastructure.task_runner import TaskRunner
from auto_video_maker.models.selected_asset import SelectedAsset
from auto_video_maker.providers.image_provider import ImageCandidate, ImageProvider
from auto_video_maker.services.asset_download_service import AssetDownloadService
from auto_video_maker.services.keyword_service import KeywordService

logger = logging.getLogger(__name__)

_THUMB_SIZE = QSize(96, 96)


class ImageSearchDialog(QDialog):
    """搜索候选图片、显示作者与许可证、选择并下载。"""

    def __init__(
        self,
        scene_text: str,
        initial_keywords: list[str],
        project_root,
        image_provider: ImageProvider,
        download_service: AssetDownloadService,
        keyword_service: KeywordService,
        task_runner: TaskRunner,
        privacy_gate=None,
        parent: QWidget | None = None,
    ) -> None:
        """privacy_gate：可选回调 () -> bool，LLM 生成前的隐私确认。"""
        super().__init__(parent)
        self._scene_text = scene_text
        self._project_root = project_root
        self._image_provider = image_provider
        self._download_service = download_service
        self._keyword_service = keyword_service
        self._task_runner = task_runner
        self._privacy_gate = privacy_gate
        self._candidates: list[ImageCandidate] = []
        self._active_task_id: int | None = None

        self.selected_asset: SelectedAsset | None = None
        # 持久化基线：保留 Scene 中已有的完整关键词列表；
        # 只有 AI 成功生成新列表时才被替换（点击推荐词不改动它）。
        self.edited_keywords: list[str] = list(initial_keywords)

        self.setWindowTitle("搜索图片")
        self.setMinimumSize(640, 520)

        # 建议列表：已保存关键词优先，否则用规则兜底；只展示不拼接
        suggestions = KeywordService.normalize_keywords(initial_keywords)
        if not suggestions:
            suggestions = KeywordService.normalize_keywords(
                keyword_service.generate_fallback(scene_text)
            )

        # 关键词行：搜索框只承载单个查询
        self.keyword_edit = QLineEdit(suggestions[0] if suggestions else "", self)
        self.keyword_edit.setPlaceholderText("输入单个搜索关键词")
        self.ai_button = QPushButton("AI 生成关键词", self)
        self.ai_button.clicked.connect(self._on_ai_keywords)
        self.ai_button.setEnabled(keyword_service.llm_availability().available)
        if not self.ai_button.isEnabled():
            self.ai_button.setToolTip(keyword_service.llm_availability().reason)
        self.search_button = QPushButton("搜索", self)
        self.search_button.clicked.connect(self._on_search)

        keyword_row = QHBoxLayout()
        keyword_row.addWidget(self.keyword_edit, 1)
        keyword_row.addWidget(self.ai_button)
        keyword_row.addWidget(self.search_button)

        # AI 推荐关键词区域：点击某个推荐词只改变搜索框内容
        self.suggestions_label = QLabel("AI 推荐：", self)
        self._suggestions_row = QHBoxLayout()
        self._suggestions_row.addStretch(1)
        self.suggestion_buttons: list[QPushButton] = []
        suggestions_line = QHBoxLayout()
        suggestions_line.addWidget(self.suggestions_label)
        suggestions_line.addLayout(self._suggestions_row, 1)
        self._set_suggestions(suggestions)

        # 候选列表
        self.result_list = QListWidget(self)
        self.result_list.setIconSize(_THUMB_SIZE)
        self.result_list.itemSelectionChanged.connect(self._on_selection_changed)

        self.status_label = QLabel("输入关键词后点击搜索。", self)
        self.status_label.setWordWrap(True)

        # 底部按钮
        self.use_button = QPushButton("使用这张图片", self)
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._on_use_selected)
        self.cancel_task_button = QPushButton("取消", self)
        self.cancel_task_button.hide()
        self.cancel_task_button.clicked.connect(self._on_cancel_task)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.reject)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.cancel_task_button)
        bottom_row.addStretch(1)
        bottom_row.addWidget(close_button)
        bottom_row.addWidget(self.use_button)

        layout = QVBoxLayout(self)
        layout.addLayout(keyword_row)
        layout.addLayout(suggestions_line)
        layout.addWidget(self.result_list, 1)
        layout.addWidget(self.status_label)
        layout.addLayout(bottom_row)

    # ------------------------------------------------------------ 关键词

    def _set_suggestions(self, keywords: list[str]) -> None:
        """展示最多 3 个可点击的推荐关键词（已经过统一规范化）。"""
        for button in self.suggestion_buttons:
            self._suggestions_row.removeWidget(button)
            button.setParent(None)  # 立即脱离界面，避免旧按钮残留
            button.deleteLater()
        self.suggestion_buttons = []
        for keyword in keywords:
            button = QPushButton(keyword, self)
            button.clicked.connect(
                lambda checked=False, kw=keyword: self.keyword_edit.setText(kw)
            )
            self.suggestion_buttons.append(button)
            self._suggestions_row.insertWidget(
                self._suggestions_row.count() - 1, button
            )
        visible = bool(keywords)
        self.suggestions_label.setVisible(visible)
        for button in self.suggestion_buttons:
            button.setVisible(True)

    def _on_ai_keywords(self) -> None:
        if self._privacy_gate is not None and not self._privacy_gate():
            return
        self._begin_busy("正在生成关键词…")
        text = self._scene_text
        self._active_task_id = self._task_runner.run(
            lambda: self._keyword_service.generate_with_llm(text),
            self._on_ai_keywords_done,
            self._on_task_error,
        )

    def _on_ai_keywords_done(self, keywords: list[str]) -> None:
        self._end_busy()
        normalized = KeywordService.normalize_keywords(keywords)
        if not normalized:
            # AI 失败/空结果：不清空搜索框、不改变 edited_keywords
            QMessageBox.information(
                self, "没有可用关键词",
                "模型没有返回可用的关键词。你可以手动填写后搜索。",
            )
            return
        # AI 成功：整体替换（绝不与 fallback / 旧列表合并，不修改传入列表）
        self.edited_keywords = list(normalized)
        self._set_suggestions(self.edited_keywords)
        self.keyword_edit.setText(self.edited_keywords[0])  # 默认第一个；不自动搜索
        self.status_label.setText("关键词已生成：点击推荐词或直接搜索。")

    # ------------------------------------------------------------ 搜索

    def _on_search(self) -> None:
        # 只提交搜索框中的当前单个查询，绝不拼接多个关键词
        query = self.keyword_edit.text().strip()
        if not query:
            QMessageBox.information(self, "缺少关键词", "请先输入搜索关键词。")
            return
        self.result_list.clear()
        self._candidates = []
        self._begin_busy("正在搜索图片…")
        provider = self._image_provider
        self._active_task_id = self._task_runner.run(
            lambda: provider.search(query),
            self._on_search_done,
            self._on_task_error,
        )

    def _on_search_done(self, candidates: list[ImageCandidate]) -> None:
        self._end_busy()
        self._candidates = candidates
        if not candidates:
            self.status_label.setText("没有找到相关图片。可以更换关键词重试。")
            return
        for index, candidate in enumerate(candidates):
            title = candidate.title or "（无标题）"
            author = candidate.author or "未知作者"
            license_text = candidate.license.upper()
            if candidate.license_version:
                license_text += f" {candidate.license_version}"
            item = QListWidgetItem(f"{title}\n作者：{author}    许可证：{license_text}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.result_list.addItem(item)
            self._load_thumbnail(item, candidate)
        self.status_label.setText(f"共 {len(candidates)} 个候选。选中后点「使用这张图片」。")

    def _load_thumbnail(self, item: QListWidgetItem, candidate: ImageCandidate) -> None:
        """异步加载缩略图；失败保持占位，不崩溃。"""
        url = candidate.preview_url
        if not url:
            return
        service = self._download_service

        def apply_thumbnail(data: bytes) -> None:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                item.setIcon(QIcon(pixmap.scaled(
                    _THUMB_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )))

        self._task_runner.run(
            lambda: service.fetch_preview(url),
            apply_thumbnail,
            lambda exc: None,  # 占位即可
        )

    # ------------------------------------------------------------ 下载

    def _on_selection_changed(self) -> None:
        self.use_button.setEnabled(bool(self.result_list.selectedItems()))

    def _on_use_selected(self) -> None:
        items = self.result_list.selectedItems()
        if not items:
            return
        index = items[0].data(Qt.ItemDataRole.UserRole)
        candidate = self._candidates[index]
        self._begin_busy("正在下载图片…")
        service = self._download_service
        root = self._project_root
        self._active_task_id = self._task_runner.run(
            lambda: service.download(candidate, root),
            self._on_download_done,
            self._on_task_error,
        )

    def _on_download_done(self, asset: SelectedAsset) -> None:
        self._end_busy()
        self.selected_asset = asset
        self.accept()

    # ------------------------------------------------------------ 任务状态

    def _on_task_error(self, exc: Exception) -> None:
        self._end_busy()
        QMessageBox.warning(self, "操作失败", str(exc))
        self.status_label.setText("操作失败，可重试或更换关键词/候选图片。")

    def _on_cancel_task(self) -> None:
        if self._active_task_id is not None:
            self._task_runner.cancel(self._active_task_id)
        self._end_busy()
        self.status_label.setText("已取消。")

    def _begin_busy(self, message: str) -> None:
        self.status_label.setText(message)
        self.search_button.setEnabled(False)
        self.ai_button.setEnabled(False)
        self.use_button.setEnabled(False)
        self.cancel_task_button.show()

    def _end_busy(self) -> None:
        self._active_task_id = None
        self.search_button.setEnabled(True)
        self.ai_button.setEnabled(self._keyword_service.llm_availability().available)
        self._on_selection_changed()
        self.cancel_task_button.hide()

    def reject(self) -> None:
        self._on_cancel_task()
        super().reject()
