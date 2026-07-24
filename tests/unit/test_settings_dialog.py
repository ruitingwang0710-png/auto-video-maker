"""设置页面测试（离屏 Qt；无法加载图形库的环境自动跳过）。

覆盖测试要求 20：Key 行为规则。
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets",
    reason="当前环境无法加载 PySide6/Qt 图形库",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication  # noqa: E402

from auto_video_maker.infrastructure.config import ConfigStore, LLMSettings  # noqa: E402
from auto_video_maker.infrastructure.secret_store import (  # noqa: E402
    FakeSecretStore,
    secret_id_for_base_url,
)
from auto_video_maker.ui.settings_dialog import (  # noqa: E402
    STATUS_CONFIGURED,
    STATUS_NOT_CONFIGURED,
    SettingsDialog,
)

BASE_URL = "https://api.example.com/v1"


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config_store(tmp_path: Path) -> ConfigStore:
    store = ConfigStore(tmp_path / "config.json")
    store.save(LLMSettings(enabled=True, base_url=BASE_URL, model="m"))
    return store


@pytest.fixture
def secret_store() -> FakeSecretStore:
    return FakeSecretStore()


def make_dialog(config_store, secret_store) -> SettingsDialog:
    return SettingsDialog(config_store, secret_store)


def test_password_field_never_prefilled(qapp, config_store, secret_store) -> None:
    secret_store.set(secret_id_for_base_url(BASE_URL), "existing-key")
    dialog = make_dialog(config_store, secret_store)
    assert dialog.api_key_edit.text() == ""  # 绝不回填
    assert dialog.key_status_label.text() == STATUS_CONFIGURED


def test_status_not_configured(qapp, config_store, secret_store) -> None:
    dialog = make_dialog(config_store, secret_store)
    assert dialog.key_status_label.text() == STATUS_NOT_CONFIGURED
    assert not dialog.delete_key_button.isEnabled()


def test_empty_save_keeps_existing_key(qapp, config_store, secret_store) -> None:
    sid = secret_id_for_base_url(BASE_URL)
    secret_store.set(sid, "existing-key")
    dialog = make_dialog(config_store, secret_store)
    dialog.api_key_edit.setText("")  # 留空保存
    dialog._on_save()
    assert secret_store.get(sid) == "existing-key"  # 原 Key 保留


def test_new_key_replaces_existing(qapp, config_store, secret_store) -> None:
    sid = secret_id_for_base_url(BASE_URL)
    secret_store.set(sid, "old-key")
    dialog = make_dialog(config_store, secret_store)
    dialog.api_key_edit.setText("new-key")
    dialog._on_save()
    assert secret_store.get(sid) == "new-key"
    assert dialog.api_key_edit.text() == ""  # 保存后清空输入框


def test_only_delete_button_deletes(qapp, config_store, secret_store) -> None:
    sid = secret_id_for_base_url(BASE_URL)
    secret_store.set(sid, "key")
    dialog = make_dialog(config_store, secret_store)
    dialog._on_delete_key()
    assert secret_store.get(sid) is None
    assert dialog.key_status_label.text() == STATUS_NOT_CONFIGURED


def test_save_writes_config_without_key(qapp, config_store, secret_store) -> None:
    dialog = make_dialog(config_store, secret_store)
    dialog.model_edit.setText("new-model")
    dialog.api_key_edit.setText("secret-value")
    dialog._on_save()
    raw = config_store.path.read_text(encoding="utf-8")
    assert "secret-value" not in raw  # Key 绝不入 config.json
    assert config_store.load().model == "new-model"


def test_status_follows_base_url_field(qapp, config_store, secret_store) -> None:
    """状态针对当前输入的 base_url 对应的 Key。"""
    secret_store.set(secret_id_for_base_url(BASE_URL), "key")
    dialog = make_dialog(config_store, secret_store)
    assert dialog.key_status_label.text() == STATUS_CONFIGURED
    dialog.base_url_edit.setText("https://other.example.com/v1")
    assert dialog.key_status_label.text() == STATUS_NOT_CONFIGURED
    dialog.base_url_edit.setText(BASE_URL)
    assert dialog.key_status_label.text() == STATUS_CONFIGURED
