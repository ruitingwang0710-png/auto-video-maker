"""FFmpegRunner 测试：定位、能力预检、路径转义（不执行真实渲染）。"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import auto_video_maker.infrastructure.ffmpeg_runner as fr
from auto_video_maker.infrastructure.config import ConfigStore, LLMSettings
from auto_video_maker.infrastructure.ffmpeg_runner import (
    CancelToken,
    FFmpegCapabilityError,
    FFmpegNotFoundError,
    FFmpegRunner,
    concat_list_line,
    escape_filter_value,
)


def make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def config_with(tmp_path: Path, **fields) -> ConfigStore:
    store = ConfigStore(tmp_path / "config.json")
    store.save(LLMSettings(**fields))
    return store


class TestLocate:
    def test_configured_ffmpeg_prefers_sibling_ffprobe(self, tmp_path, monkeypatch) -> None:
        bin_dir = tmp_path / "custom bin"
        bin_dir.mkdir()
        ffmpeg = make_executable(bin_dir / "ffmpeg")
        sibling = make_executable(bin_dir / "ffprobe")
        other = tmp_path / "other"
        other.mkdir()
        make_executable(other / "ffprobe")
        store = config_with(
            tmp_path, ffmpeg_path=str(ffmpeg), ffprobe_path=str(other / "ffprobe")
        )
        runner = FFmpegRunner(config_store=store)
        located_ffmpeg, located_ffprobe = runner.locate()
        assert located_ffmpeg == ffmpeg
        assert located_ffprobe == sibling  # 同目录优先于独立配置

    def test_configured_ffmpeg_without_sibling_uses_ffprobe_path(
        self, tmp_path
    ) -> None:
        bin_dir = tmp_path / "solo"
        bin_dir.mkdir()
        ffmpeg = make_executable(bin_dir / "ffmpeg")
        other = tmp_path / "other"
        other.mkdir()
        probe = make_executable(other / "ffprobe")
        store = config_with(
            tmp_path, ffmpeg_path=str(ffmpeg), ffprobe_path=str(probe)
        )
        located = FFmpegRunner(config_store=store).locate()
        assert located == (ffmpeg, probe)

    def test_app_bin_dir_before_path(self, tmp_path, monkeypatch) -> None:
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        ffmpeg = make_executable(bundled / "ffmpeg")
        ffprobe = make_executable(bundled / "ffprobe")
        monkeypatch.setattr(fr.shutil, "which", lambda name: "/usr/bin/" + name)
        located = FFmpegRunner(app_bin_dir=bundled).locate()
        assert located == (ffmpeg, ffprobe)

    def test_path_fallback(self, tmp_path, monkeypatch) -> None:
        system = tmp_path / "sys"
        system.mkdir()
        ffmpeg = make_executable(system / "ffmpeg")
        ffprobe = make_executable(system / "ffprobe")
        monkeypatch.setattr(
            fr.shutil, "which", lambda name: str(system / name)
        )
        located = FFmpegRunner().locate()
        assert located == (ffmpeg, ffprobe)

    def test_missing_everywhere(self, monkeypatch) -> None:
        monkeypatch.setattr(fr.shutil, "which", lambda name: None)
        with pytest.raises(FFmpegNotFoundError, match="brew install ffmpeg"):
            FFmpegRunner().locate()

    def test_configured_path_missing_raises(self, tmp_path) -> None:
        store = config_with(tmp_path, ffmpeg_path=str(tmp_path / "nope"))
        with pytest.raises(FFmpegNotFoundError, match="配置的 ffmpeg 路径不存在"):
            FFmpegRunner(config_store=store).locate()


class TestCapabilities:
    def make_runner(self, monkeypatch, encoders: str, filters: str) -> FFmpegRunner:
        runner = FFmpegRunner()
        runner._located = (Path("/fake/ffmpeg"), Path("/fake/ffprobe"))

        def fake_capture(cmd):
            if "-encoders" in cmd:
                return encoders
            if "-filters" in cmd:
                return filters
            return "ffmpeg version fake"

        monkeypatch.setattr(runner, "_capture", fake_capture)
        return runner

    FULL_ENCODERS = " V..... libx264  x\n A..... aac  x\n"
    FULL_FILTERS = (
        " ... zoompan V->V x\n T.. boxblur V->V x\n ... subtitles V->V x\n"
        " ..C scale V->V x\n TSC overlay VV->V x\n"
    )

    def test_all_present(self, monkeypatch) -> None:
        runner = self.make_runner(monkeypatch, self.FULL_ENCODERS, self.FULL_FILTERS)
        caps = runner.check_capabilities(require_subtitles=True)
        assert caps.ok

    @pytest.mark.parametrize("missing_encoder", ["libx264", "aac"])
    def test_missing_encoder_fails(self, monkeypatch, missing_encoder) -> None:
        encoders = self.FULL_ENCODERS.replace(f" {missing_encoder} ", " other ")
        runner = self.make_runner(monkeypatch, encoders, self.FULL_FILTERS)
        with pytest.raises(FFmpegCapabilityError, match=missing_encoder):
            runner.check_capabilities()

    def test_missing_subtitles_filter_fails_when_required(self, monkeypatch) -> None:
        filters = self.FULL_FILTERS.replace(" subtitles ", " nosub ")
        runner = self.make_runner(monkeypatch, self.FULL_ENCODERS, filters)
        with pytest.raises(FFmpegCapabilityError, match="subtitles"):
            runner.check_capabilities(require_subtitles=True)

    def test_missing_subtitles_ok_when_not_required(self, monkeypatch) -> None:
        filters = self.FULL_FILTERS.replace(" subtitles ", " nosub ")
        runner = self.make_runner(monkeypatch, self.FULL_ENCODERS, filters)
        caps = runner.check_capabilities(require_subtitles=False)
        assert "滤镜 subtitles" in caps.missing  # 记录缺失但不拦截


class TestPathSafety:
    """测试要求 6：中文、空格、单引号路径的转义。"""

    @pytest.mark.parametrize("raw", [
        "/tmp/我的 项目/subtitles/字幕.srt",
        "/tmp/it's a test/file.srt",
        "/tmp/plain/file.srt",
    ])
    def test_escape_filter_value_roundtrip_shape(self, raw: str) -> None:
        escaped = escape_filter_value(raw)
        assert escaped.startswith("'")
        # 单引号被安全处理（不存在裸单引号断串）
        inner = escaped[1:-1]
        assert "'" not in inner.replace(r"'\''", "")

    def test_concat_list_line(self, tmp_path) -> None:
        weird = tmp_path / "我的 视频's" / "clip_a.mp4"
        line = concat_list_line(weird)
        assert line.startswith("file '")
        assert "clip_a.mp4" in line
        assert r"'\''" in line  # 单引号转义存在

    def test_write_concat_list(self, tmp_path) -> None:
        runner = FFmpegRunner()
        paths = [tmp_path / "中 文'1.mp4", tmp_path / "b.mp4"]
        target = tmp_path / "list.txt"
        runner.write_concat_list(paths, target)
        content = target.read_text(encoding="utf-8")
        assert content.count("file '") == 2
        assert "中 文" in content

    def test_build_subtitles_filter(self, tmp_path) -> None:
        runner = FFmpegRunner()
        srt = tmp_path / "我的 字幕's.srt"
        result = runner.build_subtitles_filter(srt, "FontName=PingFang SC,Outline=2")
        assert result.startswith("subtitles=filename='")
        assert "force_style='FontName=PingFang SC,Outline=2'" in result

    def test_write_filter_script(self, tmp_path) -> None:
        runner = FFmpegRunner()
        target = tmp_path / "graph.txt"
        runner.write_filter_script("[0:v]scale=10:10[v]", target)
        assert target.read_text(encoding="utf-8") == "[0:v]scale=10:10[v]"


class TestRunCommandShape:
    def test_no_shell_and_progress_args(self, monkeypatch, tmp_path) -> None:
        captured = {}

        class FakeProcess:
            def __init__(self):
                self.stdout = iter(["out_time_us=500000\n"])
                self.stderr = iter([])
                self.returncode = 0
                self.pid = 12345
            def wait(self): return 0
            def poll(self): return 0

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return FakeProcess()

        monkeypatch.setattr(fr.subprocess, "Popen", fake_popen)
        runner = FFmpegRunner()
        runner._located = (Path("/fake/ffmpeg"), Path("/fake/ffprobe"))
        progresses: list[float] = []
        runner.run(["-i", "in.png", "out.part.mp4"],
                   expected_duration_ms=1000, on_progress=progresses.append)
        assert captured["cmd"][0] == "/fake/ffmpeg"
        assert "-progress" in captured["cmd"] and "pipe:1" in captured["cmd"]
        assert captured["kwargs"].get("start_new_session") is True
        assert "shell" not in captured["kwargs"]  # 绝不 shell=True
        assert isinstance(captured["cmd"], list)
        assert progresses and progresses[-1] == 1.0

    def test_failure_includes_stderr_tail(self, monkeypatch) -> None:
        class FakeProcess:
            def __init__(self):
                self.stdout = iter([])
                self.stderr = iter(["error line 1\n", "fatal: bad input\n"])
                self.returncode = 1
                self.pid = 1
            def wait(self): return 1
            def poll(self): return 1

        monkeypatch.setattr(fr.subprocess, "Popen", lambda *a, **k: FakeProcess())
        runner = FFmpegRunner()
        runner._located = (Path("/fake/ffmpeg"), Path("/fake/ffprobe"))
        with pytest.raises(fr.FFmpegExecutionError) as info:
            runner.run(["-i", "x", "y.part.mp4"])
        assert "fatal: bad input" in str(info.value)

    def test_cancel_terminates_group(self, monkeypatch) -> None:
        killed = []

        class FakeProcess:
            def __init__(self):
                self.stdout = iter(["out_time_us=1\n"] * 100)
                self.stderr = iter([])
                self.returncode = -15
                self.pid = 777
                self._polled = False
            def wait(self): return -15
            def poll(self): return -15

        monkeypatch.setattr(fr.subprocess, "Popen", lambda *a, **k: FakeProcess())
        monkeypatch.setattr(fr.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
        runner = FFmpegRunner()
        runner._located = (Path("/fake/ffmpeg"), Path("/fake/ffprobe"))
        token = CancelToken()
        token.cancel()
        with pytest.raises(fr.FFmpegCancelledError):
            runner.run(["-i", "x", "y.part.mp4"], cancel_token=token)
        assert killed and killed[0][0] == 777
