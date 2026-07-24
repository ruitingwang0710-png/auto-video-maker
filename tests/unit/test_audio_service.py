"""AudioService 测试：缓存键、命中再验证、隐私状态（Fake，无网络）。"""

from pathlib import Path

import pytest

from auto_video_maker.infrastructure.audio_probe import AudioProbe, AudioProbeError
from auto_video_maker.infrastructure.config import ConfigStore, LLMSettings
from auto_video_maker.providers.tts_provider import TTSProvider, TTSNetworkError, TTSVoice
from auto_video_maker.services.audio_service import (
    CURRENT_TTS_NOTICE_VERSION,
    AudioService,
    AudioServiceError,
    audio_cache_key,
)
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.scene_splitter import SceneSplitter

SCRIPT = "第一段场景文字。\n\n第二段场景文字。"


class FakeSplitter(SceneSplitter):
    def split(self, cleaned_script: str) -> list[str]:
        return ["第一段场景文字。", "第二段场景文字。"]


class FakeTTSProvider(TTSProvider):
    provider_id = "edge-tts"

    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    def list_voices(self) -> list[TTSVoice]:
        return []

    def synthesize(self, text: str, voice_id: str, rate: str, output_path: Path) -> None:
        self.calls.append((text, voice_id, rate))
        if self.fail:
            raise self.fail
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-mp3")


class FakeAudioProbe(AudioProbe):
    def __init__(self, duration: float = 2.5, fail_paths: set[str] | None = None) -> None:
        self.duration = duration
        self.fail_paths = fail_paths or set()
        self.calls: list[str] = []

    def duration_seconds(self, path: Path) -> float:
        self.calls.append(Path(path).name)
        if not Path(path).is_file():
            raise AudioProbeError("找不到音频文件")
        if Path(path).name in self.fail_paths:
            raise AudioProbeError("损坏")
        return self.duration


@pytest.fixture
def stack(tmp_path: Path):
    manager = ProjectManager()
    scene_service = SceneService(FakeSplitter(), manager)
    project = manager.create_project(
        "配音测试", SCRIPT, "9:16", tmp_path, voice="female", speech_rate="+0%"
    )
    scene_service.split_script(project)
    scene_service.save(project)
    config_store = ConfigStore(tmp_path / "config.json")
    return manager, scene_service, project, config_store


def make_service(manager, config_store, provider=None, probe=None):
    provider = provider or FakeTTSProvider()
    probe = probe or FakeAudioProbe()
    return AudioService(provider, probe, manager, config_store), provider, probe


class TestCacheKey:
    BASE = dict(provider_id="edge-tts", voice_id="zh-CN-XiaoxiaoNeural",
                rate="+0%", text="你好世界")

    def test_hash_is_24_hex(self) -> None:
        key = audio_cache_key(**self.BASE)
        assert len(key) == 24
        assert all(c in "0123456789abcdef" for c in key)

    def test_deterministic(self) -> None:
        assert audio_cache_key(**self.BASE) == audio_cache_key(**self.BASE)

    @pytest.mark.parametrize("field,value", [
        ("provider_id", "other-tts"),
        ("voice_id", "zh-CN-YunxiNeural"),
        ("rate", "+20%"),
        ("text", "你好世界！"),
        ("output_format", "wav"),
    ])
    def test_any_field_changes_hash(self, field: str, value: str) -> None:
        changed = dict(self.BASE)
        changed[field] = value
        assert audio_cache_key(**changed) != audio_cache_key(**self.BASE)


class TestGenerate:
    def test_miss_synthesizes_and_probes(self, stack) -> None:
        manager, _, project, config_store = stack
        service, provider, probe = make_service(manager, config_store)
        path, duration = service.generate_for_scene(project, 0)
        assert path.startswith("audio/tts_") and path.endswith(".mp3")
        assert duration == 2.5
        assert len(provider.calls) == 1
        assert provider.calls[0][1] == "zh-CN-XiaoxiaoNeural"  # female 解析
        project_root = manager.project_directory(project)
        assert (project_root / path).is_file()
        # 项目未被写（写入经 SceneService）
        assert project.scenes[0].audio_path is None

    def test_cache_hit_zero_synthesis(self, stack) -> None:
        manager, _, project, config_store = stack
        service, provider, _ = make_service(manager, config_store)
        path_1, _ = service.generate_for_scene(project, 0)
        path_2, _ = service.generate_for_scene(project, 0)
        assert path_1 == path_2
        assert len(provider.calls) == 1  # 第二次零合成

    def test_invalid_cache_revalidated_and_resynthesized(self, stack) -> None:
        manager, _, project, config_store = stack
        # 第一次生成
        service, provider, _ = make_service(manager, config_store)
        path, _ = service.generate_for_scene(project, 0)
        file_name = Path(path).name
        # 缓存文件被破坏：探测失败 → 重新合成
        probe_2 = FakeAudioProbe(fail_paths=set())
        calls = {"n": 0}

        class OnceFailingProbe(FakeAudioProbe):
            def duration_seconds(self, p: Path) -> float:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise AudioProbeError("损坏")
                return 3.0

        service_2, provider_2, _ = make_service(
            manager, config_store, probe=OnceFailingProbe()
        )
        path_2, duration_2 = service_2.generate_for_scene(project, 0)
        assert path_2 == path
        assert len(provider_2.calls) == 1  # 验证失败视为未命中 → 重新合成
        assert duration_2 == 3.0

    def test_synthesis_failure_propagates(self, stack) -> None:
        manager, _, project, config_store = stack
        service, _, _ = make_service(
            manager, config_store, provider=FakeTTSProvider(fail=TTSNetworkError("断网"))
        )
        with pytest.raises(TTSNetworkError):
            service.generate_for_scene(project, 0)
        assert project.scenes[0].audio_path is None

    def test_probe_failure_after_synthesis_removes_file(self, stack) -> None:
        manager, _, project, config_store = stack
        probe = FakeAudioProbe()
        probe.fail_paths = {"any"}

        class AlwaysFailProbe(FakeAudioProbe):
            def duration_seconds(self, p: Path) -> float:
                if not Path(p).is_file():
                    raise AudioProbeError("找不到")
                raise AudioProbeError("无法读取")

        service, _, _ = make_service(manager, config_store, probe=AlwaysFailProbe())
        with pytest.raises(AudioServiceError):
            service.generate_for_scene(project, 0)
        project_root = manager.project_directory(project)
        assert not list((project_root / "audio").glob("*.mp3"))  # 无效产物已清理

    def test_empty_scene_text_rejected(self, stack) -> None:
        manager, scene_service, project, config_store = stack
        scene_service.add_scene(project)  # 空场景
        service, provider, _ = make_service(manager, config_store)
        with pytest.raises(AudioServiceError, match="文字为空"):
            service.generate_for_scene(project, 2)
        assert provider.calls == []

    def test_pending_indices(self, stack) -> None:
        manager, scene_service, project, config_store = stack
        service, _, _ = make_service(manager, config_store)
        assert service.pending_indices(project) == [0, 1]
        path, duration = service.generate_for_scene(project, 0)
        scene_service.set_scene_audio(project, 0, path, duration)
        assert service.pending_indices(project) == [1]


class TestPrivacy:
    def test_needs_confirmation_initially(self, stack) -> None:
        manager, _, _, config_store = stack
        service, _, _ = make_service(manager, config_store)
        assert service.needs_privacy_confirmation()

    def test_record_then_not_needed(self, stack) -> None:
        manager, _, _, config_store = stack
        service, _, _ = make_service(manager, config_store)
        service.record_privacy_confirmation()
        assert not service.needs_privacy_confirmation()
        settings = config_store.load()
        assert settings.tts_privacy_confirmed is True
        assert settings.tts_privacy_provider == "edge-tts"
        assert settings.tts_privacy_notice_version == CURRENT_TTS_NOTICE_VERSION

    def test_provider_change_requires_reconfirmation(self, stack) -> None:
        manager, _, _, config_store = stack
        service, _, _ = make_service(manager, config_store)
        service.record_privacy_confirmation()

        class OtherProvider(FakeTTSProvider):
            provider_id = "other-tts"

        service_2, _, _ = make_service(manager, config_store, provider=OtherProvider())
        assert service_2.needs_privacy_confirmation()

    def test_notice_version_change_requires_reconfirmation(self, stack) -> None:
        manager, _, _, config_store = stack
        service, _, _ = make_service(manager, config_store)
        service.record_privacy_confirmation()
        settings = config_store.load()
        settings.tts_privacy_notice_version = 0  # 模拟旧版本确认
        config_store.save(settings)
        assert service.needs_privacy_confirmation()

    def test_independent_from_llm_privacy(self, stack) -> None:
        """TTS 确认与 LLM 确认互不影响。"""
        manager, _, _, config_store = stack
        settings = config_store.load()
        settings.privacy_confirmed_for_base_url = "https://a.com/v1"  # LLM 已确认
        config_store.save(settings)
        service, _, _ = make_service(manager, config_store)
        assert service.needs_privacy_confirmation()  # TTS 仍需确认
        service.record_privacy_confirmation()
        assert config_store.load().privacy_confirmed_for_base_url == "https://a.com/v1"
