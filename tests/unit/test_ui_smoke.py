"""UI 冒烟测试：离屏模式下首页可以构建，不崩溃。

无法导入 PySide6 的环境会自动跳过。
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets",
    reason="当前环境无法加载 PySide6/Qt 图形库",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication  # noqa: E402

from auto_video_maker.ui.main_window import APP_NAME, MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def test_main_window_constructs(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.windowTitle() == APP_NAME
    # 首页关键控件存在
    assert window.new_project_button.text() == "新建项目"
    assert window.open_project_button.text() == "打开项目"
    assert window.settings_button.text() == "设置"
    # 最近项目为空状态时不得崩溃
    assert window.recent_empty_label.isVisibleTo(window)
    assert window.recent_list.count() == 0
