"""AudioProbe 测试（使用程序构造的合法静音 MP3，无网络）。"""

from pathlib import Path

import pytest

from auto_video_maker.infrastructure.audio_probe import AudioProbe, AudioProbeError


def silent_mp3_bytes(frames: int = 40) -> bytes:
    """构造最小合法 MP3（MPEG-1 Layer III，128kbps/44100Hz）。

    每帧 417 字节 ≈ 26.1ms；40 帧 ≈ 1.04 秒。
    """
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)
    return frame * frames


class TestAudioProbe:
    def test_reads_real_duration(self, tmp_path: Path) -> None:
        target = tmp_path / "a.mp3"
        target.write_bytes(silent_mp3_bytes(40))
        duration = AudioProbe().duration_seconds(target)
        assert 0.9 < duration < 1.2

    def test_longer_file_longer_duration(self, tmp_path: Path) -> None:
        short = tmp_path / "s.mp3"
        long = tmp_path / "l.mp3"
        short.write_bytes(silent_mp3_bytes(20))
        long.write_bytes(silent_mp3_bytes(80))
        probe = AudioProbe()
        assert probe.duration_seconds(long) > probe.duration_seconds(short) * 2

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(AudioProbeError, match="找不到"):
            AudioProbe().duration_seconds(tmp_path / "nope.mp3")

    def test_corrupt_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.mp3"
        bad.write_bytes(b"this is not an mp3 file at all")
        with pytest.raises(AudioProbeError):
            AudioProbe().duration_seconds(bad)

    def test_empty_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.mp3"
        empty.write_bytes(b"")
        with pytest.raises(AudioProbeError):
            AudioProbe().duration_seconds(empty)
