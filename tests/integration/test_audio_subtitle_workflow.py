"""集成测试：拆分 → 配音（Fake TTS + 真实 mutagen）→ 字幕 → 保存 → 重开。

不发真实网络请求；mp3 为程序构造的合法静音文件。
"""

from pathlib import Path

import pytest

from auto_video_maker.infrastructure.audio_probe import AudioProbe
from auto_video_maker.infrastructure.config import ConfigStore
from auto_video_maker.providers.tts_provider import TTSProvider, TTSVoice
from auto_video_maker.services.audio_service import AudioService
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.scene_splitter import RuleBasedSceneSplitter
from auto_video_maker.services.subtitle_service import SubtitleService

SCRIPT = (
    "人工智能正在改变企业的工作方式，越来越多的公司开始采用自动化工具。\n\n"
    "这些工具可以协助处理大量重复性的日常任务，显著提高整体效率。\n\n"
    "未来已经到来。"
)


def silent_mp3_bytes(frames: int) -> bytes:
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)
    return frame * frames


class SilentMP3Provider(TTSProvider):
    """按文字长度写出不同时长的合法静音 mp3。"""

    provider_id = "edge-tts"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_voices(self) -> list[TTSVoice]:
        return []

    def synthesize(self, text: str, voice_id: str, rate: str, output_path: Path) -> None:
        self.calls.append(text)
        frames = max(20, len(text) * 3)  # 文字越长音频越长
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(silent_mp3_bytes(frames))


def build_stack(tmp_path: Path):
    manager = ProjectManager()
    scene_service = SceneService(RuleBasedSceneSplitter(), manager)
    config_store = ConfigStore(tmp_path / "config.json")
    provider = SilentMP3Provider()
    audio_service = AudioService(provider, AudioProbe(), manager, config_store)
    subtitle_service = SubtitleService()
    return manager, scene_service, audio_service, subtitle_service, provider


def test_full_audio_subtitle_workflow(tmp_path: Path) -> None:
    manager, scene_service, audio_service, subtitle_service, provider = build_stack(tmp_path)
    project = manager.create_project(
        "端到端配音", SCRIPT, "9:16", tmp_path / "out", voice="female", speech_rate="+0%"
    )
    scene_service.split_script(project)
    scene_service.save(project)
    project_root = manager.project_directory(project)

    # 1. 逐场景生成配音（模拟批量部分成功语义：每成功即写入）
    for index in audio_service.pending_indices(project):
        path, duration = audio_service.generate_for_scene(project, index)
        scene_service.set_scene_audio(project, index, path, duration)
    assert audio_service.pending_indices(project) == []
    for scene in project.scenes:
        assert scene.audio_path.startswith("audio/tts_")
        assert scene.duration > 0
        assert (project_root / scene.audio_path).is_file()
    synth_count = len(provider.calls)

    # 2. 再次全量生成：缓存命中，零合成
    for index in range(len(project.scenes)):
        path, duration = audio_service.generate_for_scene(project, index)
        scene_service.set_scene_audio(project, index, path, duration)
    assert len(provider.calls) == synth_count

    # 3. 生成字幕：Service 只产文件与路径，写入经 ProjectManager，随后保存
    relative = subtitle_service.generate(project, project_root)
    manager.set_subtitle_path(project, relative)
    scene_service.save(project)
    srt = (project_root / relative).read_text(encoding="utf-8")
    assert "-->" in srt and "人工智能" in srt

    # 4. 重开项目：audio_path/duration/subtitle_path 完整（测试 21/22）
    reloaded = manager.load_project(project_root)
    assert reloaded.output["subtitle_path"] == "subtitles/subtitles.srt"
    for scene in reloaded.scenes:
        assert scene.audio_path and scene.duration > 0
    raw = (project_root / "project.json").read_text(encoding="utf-8")
    assert str(project_root) not in raw.replace(project_root.name, "")  # 无绝对路径

    # 5. 修改文字 → 音频与字幕引用失效（核心规则端到端）
    scene_service.update_scene_text(reloaded, 0, "全新的第一场景文字。")
    assert reloaded.scenes[0].audio_path is None
    assert reloaded.output["subtitle_path"] is None
    # 其他场景配音保留；缓存文件依然存在
    assert reloaded.scenes[1].audio_path is not None
    assert list((project_root / "audio").glob("*.mp3"))

    # 6. 重新生成失效场景 → 新缓存键 → 需要一次新合成
    before = len(provider.calls)
    path, duration = audio_service.generate_for_scene(reloaded, 0)
    scene_service.set_scene_audio(reloaded, 0, path, duration)
    assert len(provider.calls) == before + 1

    # 7. 字幕重新生成后引用恢复
    relative_2 = subtitle_service.generate(reloaded, project_root)
    manager.set_subtitle_path(reloaded, relative_2)
    scene_service.save(reloaded)
    assert manager.load_project(project_root).output["subtitle_path"] == relative_2


def test_partial_failure_keeps_completed(tmp_path: Path) -> None:
    """批量部分成功语义：中途失败停止，已完成保留。"""
    from auto_video_maker.providers.tts_provider import TTSNetworkError

    class FailOnSecond(SilentMP3Provider):
        def synthesize(self, text, voice_id, rate, output_path):
            if len(self.calls) >= 1:
                self.calls.append(text)
                raise TTSNetworkError("断网")
            super().synthesize(text, voice_id, rate, output_path)

    manager = ProjectManager()
    scene_service = SceneService(RuleBasedSceneSplitter(), manager)
    audio_service = AudioService(
        FailOnSecond(), AudioProbe(), manager, ConfigStore(tmp_path / "c.json")
    )
    project = manager.create_project("部分成功", SCRIPT, "9:16", tmp_path / "out")
    scene_service.split_script(project)
    scene_service.save(project)

    completed = 0
    with pytest.raises(TTSNetworkError):
        for index in audio_service.pending_indices(project):
            path, duration = audio_service.generate_for_scene(project, index)
            scene_service.set_scene_audio(project, index, path, duration)
            completed += 1
    assert completed == 1
    assert project.scenes[0].audio_path is not None  # 已完成保留
    assert project.scenes[1].audio_path is None  # 失败及未开始保持原样
    assert scene_service.is_dirty  # 未自动保存
