"""TTSProvider / EdgeTTSProvider 测试（不发真实网络请求）。"""

from pathlib import Path

import pytest

from auto_video_maker.providers.tts_provider import (
    EdgeTTSProvider,
    TTSNetworkError,
    TTSProvider,
    TTSProviderError,
    resolve_voice_id,
)


class TestVoiceMapping:
    def test_female(self) -> None:
        assert resolve_voice_id("female") == "zh-CN-XiaoxiaoNeural"

    def test_male(self) -> None:
        assert resolve_voice_id("male") == "zh-CN-YunxiNeural"

    def test_default_maps_to_female(self) -> None:
        assert resolve_voice_id("default") == "zh-CN-XiaoxiaoNeural"

    def test_unknown_falls_back_to_female(self) -> None:
        assert resolve_voice_id("robot") == "zh-CN-XiaoxiaoNeural"


class StubEdgeProvider(EdgeTTSProvider):
    """替换真实网络合成：可注入成功字节或异常序列。"""

    def __init__(self, outcomes: list, **kwargs) -> None:
        super().__init__(**kwargs)
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str, str]] = []

    async def _synthesize_once(  # type: ignore[override]
        self, text: str, voice_id: str, rate: str, part_path: Path
    ) -> None:
        self.calls.append((text, voice_id, rate))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        part_path.write_bytes(outcome)


class TestEdgeTTSProvider:
    def test_provider_id_and_voices(self) -> None:
        provider = EdgeTTSProvider()
        assert isinstance(provider, TTSProvider)
        assert provider.provider_id == "edge-tts"
        genders = {voice.gender for voice in provider.list_voices()}
        assert genders == {"female", "male"}

    def test_success_atomic_no_part(self, tmp_path: Path) -> None:
        out = tmp_path / "a.mp3"
        provider = StubEdgeProvider([b"mp3-bytes"], sleeper=lambda s: None)
        provider.synthesize("你好", "zh-CN-XiaoxiaoNeural", "+0%", out)
        assert out.read_bytes() == b"mp3-bytes"
        assert not list(tmp_path.glob("*.part"))
        assert provider.calls == [("你好", "zh-CN-XiaoxiaoNeural", "+0%")]

    def test_network_error_retries_then_succeeds(self, tmp_path: Path) -> None:
        out = tmp_path / "a.mp3"
        provider = StubEdgeProvider(
            [TTSNetworkError("网络"), b"ok-bytes"], max_retries=1, sleeper=lambda s: None
        )
        provider.synthesize("你好", "v", "+0%", out)
        assert out.read_bytes() == b"ok-bytes"
        assert len(provider.calls) == 2

    def test_network_error_exhausts_retries(self, tmp_path: Path) -> None:
        out = tmp_path / "a.mp3"
        provider = StubEdgeProvider(
            [TTSNetworkError("x")] * 3, max_retries=2, sleeper=lambda s: None
        )
        with pytest.raises(TTSNetworkError):
            provider.synthesize("你好", "v", "+0%", out)
        assert len(provider.calls) == 3  # 1 + max_retries
        assert not out.exists()
        assert not list(tmp_path.glob("*.part"))

    def test_non_network_error_no_retry(self, tmp_path: Path) -> None:
        out = tmp_path / "a.mp3"
        provider = StubEdgeProvider(
            [TTSProviderError("参数无效"), b"unused"], max_retries=3, sleeper=lambda s: None
        )
        with pytest.raises(TTSProviderError):
            provider.synthesize("你好", "v", "+0%", out)
        assert len(provider.calls) == 1
        assert not list(tmp_path.glob("*.part"))

    def test_empty_output_treated_as_network_error(self, tmp_path: Path) -> None:
        out = tmp_path / "a.mp3"
        provider = StubEdgeProvider([b"", b"good"], max_retries=1, sleeper=lambda s: None)
        provider.synthesize("你好", "v", "+0%", out)
        assert out.read_bytes() == b"good"

    def test_empty_text_rejected_without_call(self, tmp_path: Path) -> None:
        provider = StubEdgeProvider([b"x"])
        with pytest.raises(TTSProviderError, match="文字为空"):
            provider.synthesize("   ", "v", "+0%", tmp_path / "a.mp3")
        assert provider.calls == []
