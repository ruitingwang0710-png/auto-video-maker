"""SceneService 测试：Scene 统一创建、编辑操作、覆盖保护、dirty 状态。

使用 FakeSceneSplitter 验证 SceneService 只依赖 SceneSplitter 接口。
"""

from pathlib import Path

import pytest

from auto_video_maker.models.project import Project, ProjectSettings
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import (
    SceneService,
    SceneServiceError,
    ScenesExistError,
)
from auto_video_maker.services.scene_splitter import SceneSplitter

SCRIPT = "人工智能正在改变企业的工作方式。现在，自动化工具可以协助企业处理重复任务。"


class FakeSceneSplitter(SceneSplitter):
    """测试替身：返回预设结果并记录调用。"""

    def __init__(self, result: list[str]) -> None:
        self.result = result
        self.calls: list[str] = []

    def split(self, cleaned_script: str) -> list[str]:
        self.calls.append(cleaned_script)
        return list(self.result)


@pytest.fixture
def manager() -> ProjectManager:
    return ProjectManager()


@pytest.fixture
def project(manager: ProjectManager, tmp_path: Path) -> Project:
    return manager.create_project("场景测试", SCRIPT, "9:16", tmp_path)


def make_service(
    manager: ProjectManager, texts: list[str] | None = None
) -> tuple[SceneService, FakeSceneSplitter]:
    splitter = FakeSceneSplitter(texts if texts is not None else ["场景一", "场景二", "场景三"])
    return SceneService(splitter, manager), splitter


# ------------------------------------------------------------ Scene 创建

def test_split_creates_scenes_with_default_fields(
    manager: ProjectManager, project: Project
) -> None:
    service, splitter = make_service(manager)
    scenes = service.split_script(project)
    assert len(scenes) == 3
    for index, scene in enumerate(scenes, start=1):
        assert scene.scene_id == index
        assert scene.search_keywords == []
        assert scene.selected_asset is None
        assert scene.audio_path is None
        assert scene.duration is None
        assert scene.status == "pending"
    # 拆分器收到的是清理后的文案
    assert splitter.calls == [SCRIPT]
    # original_script 保持原样
    assert project.original_script == SCRIPT


def test_scene_ids_continuous_after_operations(
    manager: ProjectManager, project: Project
) -> None:
    service, _ = make_service(manager)
    service.split_script(project)
    service.add_scene(project, "新增")
    service.delete_scene(project, 0)
    service.move_scene_up(project, 2)
    assert [scene.scene_id for scene in project.scenes] == list(
        range(1, len(project.scenes) + 1)
    )


def test_service_depends_only_on_interface(
    manager: ProjectManager, project: Project
) -> None:
    """替换拆分器实现无需修改 SceneService（测试要求 20）。"""
    service, _ = make_service(manager, ["自定义拆分结果一号", "自定义拆分结果二号"])
    scenes = service.split_script(project)
    assert [scene.text for scene in scenes] == ["自定义拆分结果一号", "自定义拆分结果二号"]


def test_empty_split_result_raises(manager: ProjectManager, project: Project) -> None:
    service, _ = make_service(manager, [])
    with pytest.raises(SceneServiceError, match="没有可拆分的内容"):
        service.split_script(project)


# ------------------------------------------------------------ 覆盖保护

def test_split_refuses_silent_overwrite(
    manager: ProjectManager, project: Project
) -> None:
    service, _ = make_service(manager)
    service.split_script(project)
    with pytest.raises(ScenesExistError, match="已有 3 个场景"):
        service.split_script(project)
    # 场景未被改动
    assert len(project.scenes) == 3


def test_split_with_confirmed_overwrite(
    manager: ProjectManager, project: Project
) -> None:
    service, splitter = make_service(manager)
    service.split_script(project)
    splitter.result = ["新场景"]
    scenes = service.split_script(project, overwrite=True)
    assert [scene.text for scene in scenes] == ["新场景"]


# ------------------------------------------------------------ 编辑操作

def test_update_scene_text(manager: ProjectManager, project: Project) -> None:
    service, _ = make_service(manager)
    service.split_script(project)
    service.update_scene_text(project, 1, "修改后的文字")
    assert project.scenes[1].text == "修改后的文字"


def test_add_and_delete_scene(manager: ProjectManager, project: Project) -> None:
    service, _ = make_service(manager)
    service.split_script(project)
    service.add_scene(project, "第四个")
    assert len(project.scenes) == 4
    assert project.scenes[3].scene_id == 4
    service.delete_scene(project, 1)
    assert len(project.scenes) == 3
    assert [scene.scene_id for scene in project.scenes] == [1, 2, 3]
    assert [scene.text for scene in project.scenes] == ["场景一", "场景三", "第四个"]


def test_move_up_and_down(manager: ProjectManager, project: Project) -> None:
    service, _ = make_service(manager)
    service.split_script(project)
    new_index = service.move_scene_up(project, 1)
    assert new_index == 0
    assert [scene.text for scene in project.scenes] == ["场景二", "场景一", "场景三"]
    new_index = service.move_scene_down(project, 1)
    assert new_index == 2
    assert [scene.text for scene in project.scenes] == ["场景二", "场景三", "场景一"]
    assert [scene.scene_id for scene in project.scenes] == [1, 2, 3]


def test_boundary_moves_are_safe_noop(
    manager: ProjectManager, project: Project
) -> None:
    """测试要求 15：第一场景上移、最后场景下移不破坏顺序。"""
    service, _ = make_service(manager)
    service.split_script(project)
    order_before = [scene.text for scene in project.scenes]
    assert service.move_scene_up(project, 0) == 0
    assert service.move_scene_down(project, 2) == 2
    assert [scene.text for scene in project.scenes] == order_before
    assert [scene.scene_id for scene in project.scenes] == [1, 2, 3]


def test_invalid_index_raises(manager: ProjectManager, project: Project) -> None:
    service, _ = make_service(manager)
    service.split_script(project)
    for operation in (
        lambda: service.update_scene_text(project, 99, "x"),
        lambda: service.delete_scene(project, -1),
        lambda: service.move_scene_up(project, 99),
        lambda: service.move_scene_down(project, 99),
    ):
        with pytest.raises(SceneServiceError):
            operation()


# ------------------------------------------------------------ dirty 状态

def test_dirty_lifecycle(manager: ProjectManager, project: Project) -> None:
    service, _ = make_service(manager)
    assert not service.is_dirty
    service.split_script(project)
    assert service.is_dirty
    service.save(project)
    assert not service.is_dirty
    service.update_scene_text(project, 0, "改动")
    assert service.is_dirty
    service.save(project)
    assert not service.is_dirty


def test_unchanged_text_does_not_mark_dirty(
    manager: ProjectManager, project: Project
) -> None:
    service, _ = make_service(manager)
    service.split_script(project)
    service.save(project)
    service.update_scene_text(project, 0, project.scenes[0].text)
    assert not service.is_dirty


def test_discard_changes_clears_dirty(
    manager: ProjectManager, project: Project
) -> None:
    service, _ = make_service(manager)
    service.split_script(project)
    service.discard_changes()
    assert not service.is_dirty


# ------------------------------------------------------------ 保存

def test_blank_scene_cannot_be_saved(
    manager: ProjectManager, project: Project
) -> None:
    service, _ = make_service(manager)
    service.split_script(project)
    service.add_scene(project)  # 空场景
    with pytest.raises(SceneServiceError, match="场景内容为空"):
        service.save(project)
    assert service.is_dirty  # 保存失败不清除未保存状态


def test_save_and_reload_scenes(
    manager: ProjectManager, project: Project, tmp_path: Path
) -> None:
    service, _ = make_service(manager)
    service.split_script(project)
    service.update_scene_text(project, 0, "编辑过的场景一")
    service.save(project)
    reloaded = manager.load_project(tmp_path / "场景测试")
    assert [scene.text for scene in reloaded.scenes] == ["编辑过的场景一", "场景二", "场景三"]
    assert [scene.scene_id for scene in reloaded.scenes] == [1, 2, 3]
    assert reloaded.original_script == SCRIPT
