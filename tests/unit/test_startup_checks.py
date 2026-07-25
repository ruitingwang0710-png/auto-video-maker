"""启动检查测试（Fake 注入，无网络、无真弹窗）。"""

import os
from pathlib import Path

import pytest

from auto_video_maker.infrastructure.config import ConfigStore
from auto_video_maker.infrastructure.ffmpeg_runner import (
    FFmpegCapabilityError,
    FFmpegNotFoundError,
    FFmpegRunner,
)
from auto_video_maker.services.startup_checks import run_startup_checks


class FakeRunner(FFmpegRunner):
    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self.error = error
        self.calls: list[bool] = []

    def check_capabilities(self, require_subtitles: bool = True):
        self.calls.append(require_subtitles)
        if self.error:
            raise self.error
        return None


def make_config(tmp_path: Path) -> ConfigStore:
    return ConfigStore(tmp_path / "conf" / "config.json")


def test_all_pass(tmp_path: Path) -> None:
    issues = run_startup_checks(FakeRunner(), make_config(tmp_path))
    assert issues == []


def test_ffmpeg_missing_reported_not_blocking(tmp_path: Path) -> None:
    runner = FakeRunner(error=FFmpegNotFoundError("未找到 FFmpeg。"))
    issues = run_startup_checks(runner, make_config(tmp_path))
    assert len(issues) == 1
    assert issues[0].category == "ffmpeg"
    assert "其余功能不受影响" in issues[0].message  # 不阻止使用（约束 D）


def test_ffmpeg_capability_missing_reported(tmp_path: Path) -> None:
    runner = FakeRunner(error=FFmpegCapabilityError(["滤镜 subtitles"]))
    issues = run_startup_checks(runner, make_config(tmp_path))
    assert len(issues) == 1
    assert "subtitles" in issues[0].message


def test_unwritable_config_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / "conf"
    config_dir.mkdir()
    config_dir.chmod(0o500)
    try:
        if os.access(config_dir, os.W_OK):
            pytest.skip("特权用户可绕过权限")
        issues = run_startup_checks(
            FakeRunner(), ConfigStore(config_dir / "config.json")
        )
        assert any(issue.category == "config" for issue in issues)
    finally:
        config_dir.chmod(0o700)


def test_no_network_and_no_secrets_in_messages(tmp_path: Path) -> None:
    """检查只依赖注入对象；消息不含环境变量或密钥信息。"""
    runner = FakeRunner(error=FFmpegNotFoundError("未找到 FFmpeg。"))
    issues = run_startup_checks(runner, make_config(tmp_path))
    combined = " ".join(issue.message for issue in issues)
    assert "sk-" not in combined and "PATH=" not in combined
    assert runner.calls == [True]  # 只做能力检查，无其他副作用
