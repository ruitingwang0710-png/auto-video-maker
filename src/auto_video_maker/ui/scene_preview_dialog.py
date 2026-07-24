"""拆分结果预览与确认对话框。

只展示与收集用户选择，不修改任何数据。
"""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class PreviewChoice(Enum):
    """用户在预览对话框中的选择。"""

    APPLY = auto()
    USE_RULES = auto()
    CANCEL = auto()


class ScenePreviewDialog(QDialog):
    """展示拆分结果（编号 + 文字），收集 应用/改用规则拆分/取消。"""

    def __init__(
        self,
        texts: list[str],
        title: str = "拆分结果预览",
        show_use_rules: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.choice = PreviewChoice.CANCEL
        self.setWindowTitle(title)
        self.setMinimumSize(560, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"共 {len(texts)} 个场景，请确认：", self))

        self.preview_list = QListWidget(self)
        for index, text in enumerate(texts, start=1):
            preview = " ".join(text.split())
            self.preview_list.addItem(f"{index:>2}. {preview}")
        layout.addWidget(self.preview_list)

        self.apply_button = QPushButton("应用", self)
        self.apply_button.clicked.connect(self._on_apply)
        self.use_rules_button = QPushButton("改用规则拆分", self)
        self.use_rules_button.clicked.connect(self._on_use_rules)
        self.use_rules_button.setVisible(show_use_rules)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self.use_rules_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def _on_apply(self) -> None:
        self.choice = PreviewChoice.APPLY
        self.accept()

    def _on_use_rules(self) -> None:
        self.choice = PreviewChoice.USE_RULES
        self.accept()
