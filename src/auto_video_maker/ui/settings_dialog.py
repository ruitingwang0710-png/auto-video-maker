"""最小 LLM 设置页面。

Key 行为规则（见 TASK.md）：
- 打开时不从钥匙串读取 Key 回填密码框
- 密码框留空保存：保留原 Key；输入新 Key 保存：替换当前地址的 Key
- 只有「删除已保存 Key」按钮才删除 Key
- 状态只显示固定文字「已配置 / 未配置」，不显示任何 Key 衍生信息
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from auto_video_maker.infrastructure.config import ConfigStore
from auto_video_maker.infrastructure.secret_store import (
    SecretStore,
    SecretStoreError,
    secret_id_for_base_url,
)

STATUS_CONFIGURED = "API Key 已配置"
STATUS_NOT_CONFIGURED = "API Key 未配置"


class SettingsDialog(QDialog):
    """智能分镜（LLM）设置。"""

    def __init__(
        self,
        config_store: ConfigStore,
        secret_store: SecretStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_store = config_store
        self._secret_store = secret_store

        self.setWindowTitle("设置 — 智能分镜")
        self.setMinimumWidth(480)

        settings = self._config_store.load()

        self.enabled_checkbox = QCheckBox("启用智能分镜", self)
        self.enabled_checkbox.setChecked(settings.enabled)

        self.base_url_edit = QLineEdit(settings.base_url, self)
        self.base_url_edit.setPlaceholderText("例如 https://api.example.com/v1")
        self.base_url_edit.textChanged.connect(self._refresh_key_status)

        self.model_edit = QLineEdit(settings.model, self)
        self.model_edit.setPlaceholderText("模型名称")

        # 安全要求：绝不回填已保存的 Key
        self.api_key_edit = QLineEdit(self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("留空表示保留已保存的 Key")

        self.key_status_label = QLabel(self)

        self.delete_key_button = QPushButton("删除已保存 Key", self)
        self.delete_key_button.clicked.connect(self._on_delete_key)

        key_status_row = QHBoxLayout()
        key_status_row.addWidget(self.key_status_label)
        key_status_row.addStretch(1)
        key_status_row.addWidget(self.delete_key_button)

        form = QFormLayout(self)
        form.addRow(self.enabled_checkbox)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("Model", self.model_edit)
        form.addRow("API Key", self.api_key_edit)
        form.addRow(key_status_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存配置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._refresh_key_status()

    # ------------------------------------------------------------ 槽函数

    def _on_save(self) -> None:
        settings = self._config_store.load()
        settings.enabled = self.enabled_checkbox.isChecked()
        settings.base_url = self.base_url_edit.text().strip()
        settings.model = self.model_edit.text().strip()

        new_key = self.api_key_edit.text()
        try:
            if new_key:  # 留空保存 = 保留原 Key；空字符串绝不覆盖或删除
                self._secret_store.set(
                    secret_id_for_base_url(settings.base_url), new_key
                )
        except SecretStoreError as exc:
            QMessageBox.warning(self, "无法保存 API Key", str(exc))
            return
        try:
            self._config_store.save(settings)
        except OSError:
            QMessageBox.warning(self, "无法保存配置", "配置文件写入失败，请重试。")
            return
        self.api_key_edit.clear()
        self._refresh_key_status()
        self.accept()

    def _on_delete_key(self) -> None:
        base_url = self.base_url_edit.text().strip()
        try:
            self._secret_store.delete(secret_id_for_base_url(base_url))
        except SecretStoreError as exc:
            QMessageBox.warning(self, "无法删除 API Key", str(exc))
            return
        self._refresh_key_status()

    # ------------------------------------------------------------ 辅助

    def _refresh_key_status(self) -> None:
        base_url = self.base_url_edit.text().strip()
        try:
            configured = self._secret_store.exists(secret_id_for_base_url(base_url))
        except SecretStoreError:
            configured = False
        self.key_status_label.setText(
            STATUS_CONFIGURED if configured else STATUS_NOT_CONFIGURED
        )
        self.delete_key_button.setEnabled(configured)
