"""video_path 项目级状态与失效矩阵测试（测试要求 15、16）。"""

from pathlib import Path

import pytest

from auto_video_maker.models.project import Project
from auto_video_maker.models.selected_asset import SelectedAsset
from auto_video_maker.services.project_manager import ProjectManager, ProjectManagerError
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.scene_splitter import SceneSplitter

SCRIPT = "第一段场景文字。\n\n第二段场景文字。"


class FakeSplitter(SceneSplitter):
    def split(self, cleaned_script: str) -> list[str]:
        return ["第一段场景文字。", "第二段场景文字。"]


@pytest.fixture
def manager() -> ProjectManager:
    return ProjectManager()


@pytest.fixture
def service(manager: ProjectManager) -> SceneService:
    return SceneService(FakeSplitter(), manager)


@pytest.fixture
def project(manager, service, tmp_path) -> Project:
    project = manager.create_project("视频矩阵", SCRIPT, "9:16", tmp_path)
    service.split_script(project)
    service.save(project)
    return project


def give_outputs(manager: ProjectManager, service: SceneService, project: Project) -> None:
    """预置字幕与视频引用（文件真实存在以通过校验）。"""
    root = manager.project_directory(project)
    (root / "subtitles").mkdir(exist_ok=True)
    (root / "subtitles" / "subtitles.srt").write_text("1\n", encoding="utf-8")
    (root / "output").mkdir(exist_ok=True)
    (root / "output" / "final_video.mp4").write_bytes(b"x")
    manager.set_subtitle_path(project, "subtitles/subtitles.srt")
    manager.set_video_path(project, "output/final_video.mp4")


def make_asset(root: Path, name: str) -> SelectedAsset:
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / name).write_bytes(b"img")
    return SelectedAsset(
        provider="openverse", source="wikimedia", asset_id=name, title="t",
        local_path=f"assets/{name}", source_page="https://e.com", author="a",
        author_url="", license="by", license_version="4.0",
        license_url="https://cc.org", attribution="attr", width=10, height=10,
    )


class TestSetClearVideoPath:
    def test_set_and_clear(self, manager, service, project) -> None:
        give_outputs(manager, service, project)
        assert project.output["video_path"] == "output/final_video.mp4"
        assert project.output["status"] == "rendered"
        manager.clear_video_path(project)
        assert project.output["video_path"] is None
        assert project.output["status"] == "draft"

    @pytest.mark.parametrize("bad", [
        "/abs/v.mp4", "../v.mp4", "output/../../v.mp4", "a\\b.mp4", " ",
    ])
    def test_invalid_paths_rejected(self, manager, project, bad) -> None:
        with pytest.raises(ProjectManagerError):
            manager.set_video_path(project, bad)
        assert project.output.get("video_path") is None

    def test_persists_across_save_reload(
        self, manager, service, project, tmp_path
    ) -> None:
        give_outputs(manager, service, project)
        service.save(project)
        reloaded = manager.load_project(tmp_path / "视频矩阵")
        assert reloaded.output["video_path"] == "output/final_video.mp4"


class TestInvalidationMatrix:
    """失效矩阵专项（含核心行：换图只清 video_path）。"""

    def test_image_change_clears_only_video_path(
        self, manager, service, project
    ) -> None:
        give_outputs(manager, service, project)
        root = manager.project_directory(project)
        service.set_scene_asset(project, 0, make_asset(root, "new.jpg"))
        assert project.output["video_path"] is None  # video 清
        assert project.output["subtitle_path"] == "subtitles/subtitles.srt"  # 字幕不清
        # 文件不删除
        assert (root / "output" / "final_video.mp4").is_file()

    def test_text_change_clears_all_three(self, manager, service, project) -> None:
        give_outputs(manager, service, project)
        service.update_scene_text(project, 0, "全新文字。")
        assert project.scenes[0].audio_path is None
        assert project.output["subtitle_path"] is None
        assert project.output["video_path"] is None

    def test_scene_set_changes_clear_subtitle_and_video(
        self, manager, service, project
    ) -> None:
        for operation in (
            lambda: service.add_scene(project, "新场景。"),
            lambda: service.delete_scene(project, len(project.scenes) - 1),
            lambda: service.move_scene_down(project, 0),
            lambda: service.replace_from_texts(project, ["替换。"], overwrite=True),
        ):
            give_outputs(manager, service, project)
            operation()
            assert project.output["subtitle_path"] is None
            assert project.output["video_path"] is None

    def test_audio_regeneration_clears_subtitle_and_video(
        self, manager, service, project
    ) -> None:
        give_outputs(manager, service, project)
        root = manager.project_directory(project)
        (root / "audio").mkdir(exist_ok=True)
        (root / "audio" / "tts_x.mp3").write_bytes(b"a")
        service.set_scene_audio(project, 0, "audio/tts_x.mp3", 2.0)
        assert project.output["subtitle_path"] is None
        assert project.output["video_path"] is None

    def test_noop_move_keeps_everything(self, manager, service, project) -> None:
        give_outputs(manager, service, project)
        service.move_scene_up(project, 0)  # 边界 no-op
        assert project.output["subtitle_path"] is not None
        assert project.output["video_path"] is not None
