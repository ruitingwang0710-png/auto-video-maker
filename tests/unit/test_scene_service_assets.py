"""SceneService 素材/关键词新方法测试（测试要求 10、15）。"""

from pathlib import Path

import pytest

from auto_video_maker.models.project import Project
from auto_video_maker.models.selected_asset import SelectedAsset
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService, SceneServiceError
from auto_video_maker.services.scene_splitter import SceneSplitter

SCRIPT = "悉尼歌剧院坐落在海边。白色的屋顶闪闪发光。"


class FakeSplitter(SceneSplitter):
    def split(self, cleaned_script: str) -> list[str]:
        return ["场景一。", "场景二。"]


@pytest.fixture
def manager() -> ProjectManager:
    return ProjectManager()


@pytest.fixture
def service(manager: ProjectManager) -> SceneService:
    return SceneService(FakeSplitter(), manager)


@pytest.fixture
def project(manager: ProjectManager, service: SceneService, tmp_path: Path) -> Project:
    project = manager.create_project("素材测试", SCRIPT, "9:16", tmp_path)
    service.split_script(project)
    service.save(project)
    return project


def make_asset(local_path: str = "assets/openverse_abc.jpg") -> SelectedAsset:
    return SelectedAsset(
        provider="openverse",
        source="wikimedia",
        asset_id="abc",
        title="t",
        local_path=local_path,
        source_page="https://example.com/p",
        author="a",
        author_url="",
        license="by",
        license_version="4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="attr",
        width=100,
        height=80,
    )


class TestSetSceneKeywords:
    def test_set_keywords(self, service: SceneService, project: Project) -> None:
        service.set_scene_keywords(project, 0, ["sydney opera house"])
        assert project.scenes[0].search_keywords == ["sydney opera house"]
        assert service.is_dirty

    @pytest.mark.parametrize("bad", ["str", [""], ["ok", "  "], [1], None])
    def test_invalid_keywords_rejected(self, service, project, bad) -> None:
        before = list(project.scenes[0].search_keywords)
        with pytest.raises(SceneServiceError):
            service.set_scene_keywords(project, 0, bad)
        assert project.scenes[0].search_keywords == before
        assert not service.is_dirty

    def test_unchanged_keywords_not_dirty(self, service, project) -> None:
        service.set_scene_keywords(project, 0, ["kw"])
        service.save(project)
        service.set_scene_keywords(project, 0, ["kw"])
        assert not service.is_dirty


class TestSetSceneAsset:
    def test_set_asset(self, service: SceneService, project: Project) -> None:
        service.set_scene_asset(project, 0, make_asset())
        stored = project.scenes[0].selected_asset
        assert stored["provider"] == "openverse"
        assert stored["local_path"] == "assets/openverse_abc.jpg"
        assert stored["license"] == "by"
        assert service.is_dirty

    def test_dict_rejected(self, service: SceneService, project: Project) -> None:
        with pytest.raises(SceneServiceError, match="SelectedAsset"):
            service.set_scene_asset(project, 0, make_asset().to_dict())
        assert project.scenes[0].selected_asset is None
        assert not service.is_dirty

    def test_path_escape_rejected_state_unchanged(
        self, service: SceneService, project: Project, manager: ProjectManager
    ) -> None:
        """测试要求 15：拒绝逃逸路径且项目状态不变。

        '../outside.jpg' 在模型构造期即被拒绝；符号链接逃逸在
        set_scene_asset 的 resolve_within 检查中被拒绝。
        """
        import os

        from auto_video_maker.models.selected_asset import AssetValidationError

        # 构造期拦截 ../
        with pytest.raises(AssetValidationError):
            make_asset(local_path="../outside.jpg")

        # 符号链接逃逸在写入期拦截
        project_dir = manager.project_directory(project)
        outside = project_dir.parent / "outside_dir"
        outside.mkdir(exist_ok=True)
        os.symlink(outside, project_dir / "assets" / "leak")
        sneaky = make_asset(local_path="assets/leak/pic.jpg")
        with pytest.raises(SceneServiceError, match="项目目录之外"):
            service.set_scene_asset(project, 0, sneaky)
        assert project.scenes[0].selected_asset is None
        assert not service.is_dirty

    def test_invalid_index(self, service: SceneService, project: Project) -> None:
        with pytest.raises(SceneServiceError):
            service.set_scene_asset(project, 99, make_asset())


class TestBackwardCompatibility:
    def test_load_old_project_with_null_assets(
        self, manager: ProjectManager, service: SceneService, project: Project, tmp_path: Path
    ) -> None:
        """测试要求 16：selected_asset=null 的旧项目加载、保存、再打开。"""
        # Phase 2 时代保存的项目：selected_asset 均为 null
        loaded = manager.load_project(tmp_path / "素材测试")
        assert all(scene.selected_asset is None for scene in loaded.scenes)
        # 保存后仍可重新打开
        service_2 = SceneService(FakeSplitter(), manager)
        service_2.update_scene_text(loaded, 0, "修改后的场景。")
        service_2.save(loaded)
        reloaded = manager.load_project(tmp_path / "素材测试")
        assert reloaded.scenes[0].text == "修改后的场景。"
        assert reloaded.scenes[0].selected_asset is None

    def test_asset_persists_across_save_reload(
        self, manager: ProjectManager, service: SceneService, project: Project, tmp_path: Path
    ) -> None:
        service.set_scene_asset(project, 0, make_asset())
        service.save(project)
        reloaded = manager.load_project(tmp_path / "素材测试")
        stored = reloaded.scenes[0].selected_asset
        assert stored["asset_id"] == "abc"
        assert stored["attribution"] == "attr"
        assert stored["local_path"] == "assets/openverse_abc.jpg"
        assert not str(stored["local_path"]).startswith("/")
