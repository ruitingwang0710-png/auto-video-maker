"""ResourceLocator 测试：开发态与模拟 frozen 双态。"""

import sys
from pathlib import Path

from auto_video_maker.infrastructure import resource_locator as rl


def freeze(monkeypatch, tmp_path: Path) -> Path:
    """模拟 PyInstaller .app 布局并冻结。"""
    macos_dir = tmp_path / "Auto Video Maker.app" / "Contents" / "MacOS"
    (macos_dir / "bin").mkdir(parents=True)
    resources = tmp_path / "Auto Video Maker.app" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    executable = macos_dir / "Auto Video Maker"
    executable.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    return tmp_path / "Auto Video Maker.app"


class TestDevelopment:
    def test_not_frozen_returns_none(self, monkeypatch) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        assert rl.is_frozen() is False
        assert rl.bundled_bin_dir() is None
        assert rl.bundled_resources_dir() is None
        assert rl.bundled_file("icon.icns") is None


class TestFrozen:
    def test_bin_dir_is_macos_bin(self, monkeypatch, tmp_path) -> None:
        app = freeze(monkeypatch, tmp_path)
        assert rl.is_frozen() is True
        # 不依赖 _MEIPASS：直接由可执行文件定位 Contents/MacOS/bin
        assert rl.bundled_bin_dir() == app / "Contents" / "MacOS" / "bin"

    def test_resources_dir(self, monkeypatch, tmp_path) -> None:
        app = freeze(monkeypatch, tmp_path)
        assert rl.bundled_resources_dir() == app / "Contents" / "Resources"

    def test_bundled_file_exists_and_missing(self, monkeypatch, tmp_path) -> None:
        app = freeze(monkeypatch, tmp_path)
        target = app / "Contents" / "Resources" / "THIRD_PARTY_NOTICES.txt"
        target.write_text("notices", encoding="utf-8")
        assert rl.bundled_file("THIRD_PARTY_NOTICES.txt") == target
        assert rl.bundled_file("missing.bin") is None

    def test_meipass_not_used(self, monkeypatch, tmp_path) -> None:
        """即使存在 _MEIPASS 也不用于 bin 定位（裁决 C）。"""
        app = freeze(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "elsewhere"), raising=False)
        assert rl.bundled_bin_dir() == app / "Contents" / "MacOS" / "bin"
