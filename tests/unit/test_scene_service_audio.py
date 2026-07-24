"""SceneService 配音扩展与派生产物失效规则测试（测试要求 10–15）。"""

from pathlib import Path

import pytest

from auto_video_maker.models.project import Project
from auto_video_maker.services.project_manager import ProjectManager, ProjectManagerError
from auto_video_maker.services.scene_service import SceneService, SceneServiceError
from auto_video_maker.services.scene_splitter import SceneSplitter

SCRIPT = "第一段场景文字。\n\n第二段场景文字。\n\n第三段场景文字。"


class FakeSplitter(SceneSplitter):
    def split(self, cleaned_script: str) -> list[str]:
        return ["第一段场景文字。", "第二段场景文字。", "第三段场景文字。"]


@pytest.fixture
def manager() -> ProjectManager:
    return ProjectManager()


@pytest.fixture
def service(manager: ProjectManager) -> SceneService:
    return SceneService(FakeSplitter(), manager)


@pytest.fixture
def project(manager: ProjectManager, service: SceneService, tmp_path: Path) -> Project:
    project = manager.create_project("失效规则", SCRIPT, "9:16", tmp_path)
    service.split_script(project)
    service.save(project)
    return project


def give_audio(service: SceneService, project: Project, index: int) -> None:
    audio_dir = service.project_manager.project_directory(project) / "audio"
    audio_dir.mkdir(exist_ok=True)
    (audio_dir / f"tts_fake{index}.mp3").write_bytes(b"x")
    service.set_scene_audio(project, index, f"audio/tts_fake{index}.mp3", 2.0)


def give_subtitle(service: SceneService, project: Project) -> None:
    root = service.project_manager.project_directory(project)
    (root / "subtitles").mkdir(exist_ok=True)
    (root / "subtitles" / "subtitles.srt").write_text("1\n", encoding="utf-8")
    service.project_manager.set_subtitle_path(project, "subtitles/subtitles.srt")


class TestSetSceneAudio:
    def test_success(self, service, project) -> None:
        give_audio(service, project, 0)
        scene = project.scenes[0]
        assert scene.audio_path == "audio/tts_fake0.mp3"
        assert scene.duration == 2.0
        assert service.is_dirty

    @pytest.mark.parametrize("bad_path", [
        "/abs/a.mp3", "../out.mp3", "audio/../../x.mp3", "a\\b.mp3", "  ", None,
    ])
    def test_invalid_path_zero_side_effects(self, service, project, bad_path) -> None:
        service.save(project)
        with pytest.raises(SceneServiceError):
            service.set_scene_audio(project, 0, bad_path, 2.0)
        assert project.scenes[0].audio_path is None
        assert not service.is_dirty

    @pytest.mark.parametrize("bad_duration", [0, -1.5, "2.0", None, True])
    def test_invalid_duration_rejected(self, service, project, bad_duration) -> None:
        with pytest.raises(SceneServiceError, match="时长|路径"):
            service.set_scene_audio(project, 0, "audio/a.mp3", bad_duration)
        assert project.scenes[0].duration is None

    def test_invalid_index(self, service, project) -> None:
        with pytest.raises(SceneServiceError):
            service.set_scene_audio(project, 99, "audio/a.mp3", 2.0)


class TestInvalidationRules:
    def test_text_change_clears_audio_and_subtitle(self, service, project) -> None:
        """测试 10：改文字 → 该场景音频引用清空 + 字幕引用清空。"""
        give_audio(service, project, 0)
        give_audio(service, project, 1)
        give_subtitle(service, project)
        service.save(project)

        service.update_scene_text(project, 0, "修改后的文字。")

        assert project.scenes[0].audio_path is None
        assert project.scenes[0].duration is None
        # 其他场景音频保留
        assert project.scenes[1].audio_path is not None
        assert project.output["subtitle_path"] is None
        assert service.is_dirty
        # 缓存文件不被删除（只清引用）
        root = service.project_manager.project_directory(project)
        assert (root / "audio" / "tts_fake0.mp3").is_file()

    def test_unchanged_text_no_invalidation(self, service, project) -> None:
        give_audio(service, project, 0)
        give_subtitle(service, project)
        service.save(project)
        service.update_scene_text(project, 0, project.scenes[0].text)  # 未变化
        assert project.scenes[0].audio_path is not None
        assert project.output["subtitle_path"] == "subtitles/subtitles.srt"
        assert not service.is_dirty

    def test_add_delete_move_clear_subtitle(self, service, project) -> None:
        """测试 11：增/删/重排 → 字幕引用清空。"""
        for operation in (
            lambda: service.add_scene(project, "新场景。"),
            lambda: service.delete_scene(project, len(project.scenes) - 1),
            lambda: service.move_scene_down(project, 0),
        ):
            give_subtitle(service, project)
            assert project.output["subtitle_path"]
            operation()
            assert project.output["subtitle_path"] is None

    def test_boundary_move_noop_keeps_subtitle(self, service, project) -> None:
        give_subtitle(service, project)
        service.move_scene_up(project, 0)  # no-op
        assert project.output["subtitle_path"] == "subtitles/subtitles.srt"

    def test_regenerate_audio_clears_subtitle(self, service, project) -> None:
        """测试 12：重新生成任一场景音频 → 字幕引用清空。"""
        give_audio(service, project, 0)
        give_subtitle(service, project)
        give_audio(service, project, 1)  # set_scene_audio 触发失效
        assert project.output["subtitle_path"] is None

    def test_replace_scenes_clears_subtitle(self, service, project) -> None:
        give_subtitle(service, project)
        service.replace_from_texts(project, ["全新场景。"], overwrite=True)
        assert project.output["subtitle_path"] is None


class TestProjectManagerSubtitlePath:
    """测试 15：项目状态 Service 正确写入和清除 subtitle_path。"""

    def test_set_and_clear(self, manager, service, project) -> None:
        give_subtitle(service, project)
        assert project.output["subtitle_path"] == "subtitles/subtitles.srt"
        manager.clear_subtitle_path(project)
        assert project.output["subtitle_path"] is None

    @pytest.mark.parametrize("bad", [
        "/abs/s.srt", "../s.srt", "subtitles/../../s.srt", "a\\b.srt", "  ",
    ])
    def test_invalid_paths_rejected(self, manager, project, bad) -> None:
        with pytest.raises(ProjectManagerError):
            manager.set_subtitle_path(project, bad)
        assert project.output.get("subtitle_path") is None

    def test_persists_across_save_reload(
        self, manager, service, project, tmp_path
    ) -> None:
        """测试 21：subtitle_path 随保存持久化。"""
        give_audio(service, project, 0)
        give_subtitle(service, project)
        service.save(project)
        reloaded = manager.load_project(tmp_path / "失效规则")
        assert reloaded.output["subtitle_path"] == "subtitles/subtitles.srt"
        assert reloaded.scenes[0].audio_path == "audio/tts_fake0.mp3"
        assert reloaded.scenes[0].duration == 2.0


class TestSpeechRateModel:
    """测试 1/2/22：speech_rate 与 voice 的模型行为、向后兼容。"""

    def test_create_project_stores_internal_values(self, manager, tmp_path) -> None:
        project = manager.create_project(
            "语速", "文字。", "9:16", tmp_path, voice="male", speech_rate="+20%"
        )
        assert project.settings.voice == "male"
        assert project.settings.speech_rate == "+20%"

    def test_invalid_rate_falls_back(self, manager, tmp_path) -> None:
        project = manager.create_project(
            "语速2", "文字。", "9:16", tmp_path, voice="female", speech_rate="fast"
        )
        assert project.settings.speech_rate == "+0%"

    def test_old_project_without_speech_rate_loads(
        self, manager, service, project, tmp_path
    ) -> None:
        """Phase 3 项目（无 speech_rate 字段）正常加载。"""
        import json

        project_file = tmp_path / "失效规则" / "project.json"
        data = json.loads(project_file.read_text(encoding="utf-8"))
        data["settings"].pop("speech_rate", None)
        project_file.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        reloaded = manager.load_project(tmp_path / "失效规则")
        assert reloaded.settings.speech_rate == "+0%"

    def test_illegal_rate_in_file_falls_back(
        self, manager, service, project, tmp_path
    ) -> None:
        import json

        project_file = tmp_path / "失效规则" / "project.json"
        data = json.loads(project_file.read_text(encoding="utf-8"))
        data["settings"]["speech_rate"] = "x300%"
        project_file.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        reloaded = manager.load_project(tmp_path / "失效规则")
        assert reloaded.settings.speech_rate == "+0%"
