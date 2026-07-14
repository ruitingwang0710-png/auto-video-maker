"""UI 冒烟测试：离屏模式下首页与场景页可以构建，不崩溃。

无法加载 PySide6/Qt 图形库的环境会自动跳过。
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

from auto_video_maker.services.project_manager import ProjectManager  # noqa: E402
from auto_video_maker.services.scene_service import SceneService  # noqa: E402
from auto_video_maker.services.scene_splitter import RuleBasedSceneSplitter  # noqa: E402
from auto_video_maker.ui.main_window import APP_NAME, MainWindow  # noqa: E402
from auto_video_maker.ui.scene_page import ScenePage  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def manager() -> ProjectManager:
    return ProjectManager()


@pytest.fixture
def scene_service(manager: ProjectManager) -> SceneService:
    # 与 app.py composition root 相同的注入链
    return SceneService(RuleBasedSceneSplitter(), manager)


def test_main_window_constructs(
    qapp: QApplication, manager: ProjectManager, scene_service: SceneService
) -> None:
    window = MainWindow(manager, scene_service)
    assert window.windowTitle() == APP_NAME
    # 首页关键控件存在
    assert window.new_project_button.text() == "新建项目"
    assert window.open_project_button.text() == "打开项目"
    assert window.settings_button.text() == "设置"
    # 最近项目为空状态时不得崩溃
    assert window.recent_empty_label.isVisibleTo(window)
    assert window.recent_list.count() == 0


def test_scene_page_constructs_and_operates(
    qapp: QApplication,
    manager: ProjectManager,
    scene_service: SceneService,
    tmp_path: Path,
) -> None:
    project = manager.create_project(
        "冒烟项目", "第一段场景文字内容测试。\n\n第二段场景文字内容测试。", "9:16", tmp_path
    )
    page = ScenePage(project, scene_service)
    # 空场景列表不得崩溃
    assert page.scene_list.count() == 0
    # 通过 service 拆分后刷新
    scene_service.split_script(project)
    page._refresh_list(select_row=0)
    assert page.scene_list.count() == len(project.scenes)
    # 边界按钮状态：第一项上移禁用
    assert not page.move_up_button.isEnabled()
    if page.scene_list.count() > 1:
        assert page.move_down_button.isEnabled()
    # 保存后 dirty 清除，页面可以直接关闭（不弹未保存对话框）
    scene_service.save(project)
    assert not scene_service.is_dirty
    page.close()
