"""VideoRenderService 测试（FakeFFmpegRunner，不执行真实渲染）。"""

import io
import math
from pathlib import Path

import pytest
from PIL import Image

import auto_video_maker.services.video_render_service as vrs
from auto_video_maker.infrastructure.config import ConfigStore
from auto_video_maker.infrastructure.ffmpeg_runner import (
    FFmpegCapabilityError,
    FFmpegExecutionError,
    FFmpegRunner,
    concat_list_line,
)
from auto_video_maker.services.credits_service import CreditsService
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.scene_splitter import SceneSplitter
from auto_video_maker.services.subtitle_service import SubtitleService
from auto_video_maker.services.video_render_service import (
    ExportValidationError,
    VideoRenderService,
    frame_count_for,
)

SCRIPT = "第一段场景的文字内容。\n\n第二段场景的文字内容。"


class FakeSplitter(SceneSplitter):
    def split(self, cleaned_script: str) -> list[str]:
        return ["第一段场景的文字内容。", "第二段场景的文字内容。"]


def good_probe(width=1080, height=1920, duration_s=2.0):
    return {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": width,
             "height": height, "pix_fmt": "yuv420p", "r_frame_rate": "30/1"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": str(duration_s)},
    }


class FakeFFmpegRunner(FFmpegRunner):
    """记录调用；run() 生成占位输出文件；probe 可配置。"""

    def __init__(self) -> None:
        super().__init__()
        self.commands: list[list[str]] = []
        self.steps: list[str] = []
        self.probe_results: dict[str, dict] = {}
        self.default_probe = good_probe
        self.capability_error: Exception | None = None
        self.fail_on_step: str | None = None

    def check_capabilities(self, require_subtitles: bool = True):
        self.steps.append(f"capabilities(subtitles={require_subtitles})")
        if self.capability_error:
            raise self.capability_error
        return None

    def run(self, args, expected_duration_ms=None, on_progress=None,
            cancel_token=None, step_name="FFmpeg") -> None:
        self.commands.append(list(args))
        self.steps.append(step_name)
        if self.fail_on_step and self.fail_on_step in step_name:
            raise FFmpegExecutionError(f"{step_name} 执行失败。", "fake stderr")
        output = Path(args[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-mp4")
        if on_progress:
            on_progress(1.0)

    clip_duration_s = 2.0
    total_duration_s = 4.0

    def probe(self, path: Path) -> dict:
        self.steps.append(f"probe({Path(path).name})")
        name = Path(path).name
        if name in self.probe_results:
            return self.probe_results[name]
        # 默认：片段返回单场景时长；concat/最终输出返回总时长
        if name.startswith(("concat", "final_video")):
            return good_probe(duration_s=self.total_duration_s)
        return good_probe(duration_s=self.clip_duration_s)


def image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def stack(tmp_path: Path):
    manager = ProjectManager()
    scene_service = SceneService(FakeSplitter(), manager)
    project = manager.create_project("导出测试", SCRIPT, "9:16", tmp_path)
    scene_service.split_script(project)
    root = manager.project_directory(project)
    # 预置素材与配音
    (root / "assets").mkdir(exist_ok=True)
    (root / "audio").mkdir(exist_ok=True)
    for index, scene in enumerate(project.scenes):
        image = root / "assets" / f"img{index}.jpg"
        image.write_bytes(image_bytes())
        audio = root / "audio" / f"tts_{index}.mp3"
        audio.write_bytes(b"audio" + bytes([index]))
        scene.selected_asset = {
            "provider": "openverse", "source": "s", "asset_id": f"a{index}",
            "title": "t", "local_path": f"assets/img{index}.jpg",
            "source_page": "", "author": "", "author_url": "",
            "license": "by", "license_version": "", "license_url": "",
            "attribution": "", "width": 40, "height": 30,
        }
        scene.audio_path = f"audio/tts_{index}.mp3"
        scene.duration = 2.0
    scene_service.save(project)
    ffmpeg = FakeFFmpegRunner()
    service = VideoRenderService(
        ffmpeg, SubtitleService(),
        CreditsService(timestamp_provider=lambda: "2026-07-25T10:00:00+10:00"),
        manager, scene_service,
    )
    return manager, scene_service, project, ffmpeg, service, root


class TestFrameCount:
    def test_ceil_used_for_non_integer(self) -> None:
        """测试 20：非整数帧数必须向上取整，视频轨不得短于音频。"""
        # 1001ms × 30 / 1000 = 30.03 → 31 帧
        assert frame_count_for(1001, 30) == 31
        assert frame_count_for(1001, 30) * 1000 / 30 >= 1001  # 视频 ≥ 音频
        # 2500ms → 75 帧整
        assert frame_count_for(2500, 30) == 75
        assert frame_count_for(1, 30) == 1
        for ms in (333, 999, 1500, 2467):
            frames = frame_count_for(ms, 30)
            assert frames == math.ceil(ms * 30 / 1000)
            assert frames * 1000 / 30 >= ms


class TestValidateExport:
    def test_ok_when_complete(self, stack) -> None:
        *_, service, _ = stack
        assert service.validate_export(stack[2]) == []

    def test_missing_image_listed(self, stack) -> None:
        manager, scene_service, project, ffmpeg, service, root = stack
        (root / "assets" / "img0.jpg").unlink()
        issues = service.validate_export(project)
        assert any("场景 1" in issue and "图片" in issue for issue in issues)

    def test_missing_audio_listed(self, stack) -> None:
        manager, scene_service, project, ffmpeg, service, root = stack
        project.scenes[1].audio_path = None
        issues = service.validate_export(project)
        assert any("场景 2" in issue and "配音" in issue for issue in issues)

    def test_capability_error_listed(self, stack) -> None:
        manager, scene_service, project, ffmpeg, service, root = stack
        ffmpeg.capability_error = FFmpegCapabilityError(["滤镜 subtitles"])
        issues = service.validate_export(project)
        assert any("subtitles" in issue for issue in issues)

    def test_render_refuses_on_issues(self, stack) -> None:
        manager, scene_service, project, ffmpeg, service, root = stack
        project.scenes[0].audio_path = None
        with pytest.raises(ExportValidationError, match="场景 1"):
            service.render(project)


class TestCommandConstruction:
    def test_clip_command_shape(self, stack) -> None:
        """测试 3/4/5/21：片段命令、编码参数、单次编码、.part.mp4。"""
        manager, scene_service, project, ffmpeg, service, root = stack
        service.render(project)
        clip_cmds = [c for c in ffmpeg.commands if "-filter_complex_script" in c]
        assert len(clip_cmds) == 2
        cmd = clip_cmds[0]
        # 编码参数
        for expected in ("libx264", "medium", "20", "yuv420p", "aac", "192k"):
            assert expected in cmd
        assert "-r" in cmd and "30" in cmd
        # 不使用 -shortest（音频为权威来源，不得截断）
        assert "-shortest" not in cmd
        # 临时输出为 .part.mp4 且显式 -f mp4
        assert cmd[-1].endswith(".part.mp4")
        assert "-f" in cmd and "mp4" in cmd
        # filtergraph 内容：contain 不拉伸 + 模糊背景 + zoompan + 字幕单次编码
        script = (root / "temp").rglob("filter_*.txt")
        # staging 已清理；改从命令侧验证 script 路径曾被传入
        script_arg = cmd[cmd.index("-filter_complex_script") + 1]
        assert script_arg.endswith(".txt")

    def test_filtergraph_content(self, stack) -> None:
        manager, scene_service, project, ffmpeg, service, root = stack
        scene = project.scenes[0]
        cues = SubtitleService().build_cues([scene])
        graph = service._build_clip_filtergraph(1080, 1920, 60, None)
        assert "force_original_aspect_ratio=decrease" in graph  # contain 不拉伸
        assert "force_original_aspect_ratio=increase" in graph  # cover 背景
        assert "boxblur=20:2" in graph
        assert "zoompan=z='1+0.08*on/59':d=60" in graph  # ceil 帧数驱动
        assert "s=1080x1920" in graph and "fps=30" in graph

    def test_final_range_conversion_without_subtitles(self, stack) -> None:
        """关闭字幕路径：链末端仍执行 full→limited + format=yuv420p。"""
        manager, scene_service, project, ffmpeg, service, root = stack
        graph = service._build_clip_filtergraph(1080, 1920, 60, None)
        final_chain = graph.split(";\n")[-1]
        assert "scale=in_range=pc:out_range=tv" in final_chain
        assert "format=yuv420p" in final_chain
        assert final_chain.endswith("[vout]")
        assert "subtitles" not in graph

    def test_subtitles_burned_before_final_conversion(self, stack, tmp_path) -> None:
        """启用字幕路径：subtitles 在前，range/format 转换必须在其后。"""
        manager, scene_service, project, ffmpeg, service, root = stack
        srt = tmp_path / "local.srt"
        graph = service._build_clip_filtergraph(1080, 1920, 60, srt)
        assert "subtitles=filename=" in graph
        assert "PingFang SC" in graph
        # 顺序：subtitles → 末端转换
        assert graph.index("subtitles=") < graph.index("scale=in_range=pc:out_range=tv")
        final_chain = graph.split(";\n")[-1]
        assert "scale=in_range=pc:out_range=tv" in final_chain
        assert "format=yuv420p" in final_chain
        assert final_chain.endswith("[vout]")
        # format 不得出现在 subtitles 之前的任何链段
        for chain in graph.split(";\n")[:-1]:
            assert "format=yuv420p" not in chain

    def test_output_args_pix_fmt_and_color_range(self, stack) -> None:
        manager, scene_service, project, ffmpeg, service, root = stack
        service.render(project)
        cmd = next(c for c in ffmpeg.commands if "-filter_complex_script" in c)
        assert "-pix_fmt" in cmd
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
        assert "-color_range" in cmd
        assert cmd[cmd.index("-color_range") + 1] == "tv"

    def test_validator_rejects_yuvj420p(self, stack) -> None:
        """验证器继续严格拒绝 yuvj420p（macOS FFmpeg 8 回归锚点）。"""
        manager, scene_service, project, ffmpeg, service, root = stack
        info = good_probe(duration_s=2.0)
        info["streams"][0]["pix_fmt"] = "yuvj420p"
        failures, diagnostics = service._validate_media(info, 1080, 1920, 2000, 50)
        assert "pix_fmt" in failures
        assert diagnostics["pix_fmt"] == "yuvj420p"

    def test_concat_stream_copy_and_remux_only(self, stack) -> None:
        """测试 5：concat 用 stream copy；最终仅 remux 不再编码。"""
        manager, scene_service, project, ffmpeg, service, root = stack
        service.render(project)
        concat_cmd = next(c for c in ffmpeg.commands if "concat" in c)
        assert "-c" in concat_cmd and "copy" in concat_cmd
        assert "libx264" not in concat_cmd
        remux_cmd = next(c for c in ffmpeg.commands if "+faststart" in c)
        assert "copy" in remux_cmd and "libx264" not in remux_cmd

    def test_no_subtitle_flag_skips_burn_but_writes_sidecar(self, stack) -> None:
        """测试 5：subtitle_enabled=False 无烧录但仍输出 sidecar SRT。"""
        manager, scene_service, project, ffmpeg, service, root = stack
        project.settings.subtitle_enabled = False
        service.render(project)
        clip_cmds = [c for c in ffmpeg.commands if "-filter_complex_script" in c]
        assert clip_cmds  # 仍渲染
        assert (root / "output" / "subtitles.srt").is_file()  # sidecar 存在
        # capabilities 检查未要求 subtitles
        assert "capabilities(subtitles=False)" in ffmpeg.steps


class TestCacheKey:
    def test_content_change_invalidates(self, stack) -> None:
        """测试 7：文件内容变化但路径不变 → 缓存键变化。"""
        manager, scene_service, project, ffmpeg, service, root = stack
        scene = project.scenes[0]
        image = root / scene.selected_asset["local_path"]
        audio = root / scene.audio_path
        cues = SubtitleService().build_cues([scene])
        key_1 = service.clip_cache_key(image, audio, 2000, "1080x1920", True, cues)
        image.write_bytes(image_bytes() + b"CHANGED")
        key_2 = service.clip_cache_key(image, audio, 2000, "1080x1920", True, cues)
        assert key_1 != key_2
        audio.write_bytes(b"different audio")
        key_3 = service.clip_cache_key(image, audio, 2000, "1080x1920", True, cues)
        assert key_3 != key_2

    def test_subtitle_factors_in_key(self, stack) -> None:
        manager, scene_service, project, ffmpeg, service, root = stack
        scene = project.scenes[0]
        image = root / scene.selected_asset["local_path"]
        audio = root / scene.audio_path
        cues = SubtitleService().build_cues([scene])
        key_on = service.clip_cache_key(image, audio, 2000, "1080x1920", True, cues)
        key_off = service.clip_cache_key(image, audio, 2000, "1080x1920", False, [])
        assert key_on != key_off
        # 局部时间轴变化
        cues_2 = SubtitleService().build_cues([scene])
        cues_2[0].end_ms += 100
        key_shift = service.clip_cache_key(image, audio, 2000, "1080x1920", True, cues_2)
        assert key_shift != key_on

    def test_key_is_24_hex(self, stack) -> None:
        manager, scene_service, project, ffmpeg, service, root = stack
        scene = project.scenes[0]
        key = service.clip_cache_key(
            root / scene.selected_asset["local_path"], root / scene.audio_path,
            2000, "1080x1920", False, [],
        )
        assert len(key) == 24 and all(c in "0123456789abcdef" for c in key)

    def test_cache_hit_skips_render_but_validates(self, stack) -> None:
        """测试 8：命中经 ffprobe 验证；不符则重渲染。"""
        manager, scene_service, project, ffmpeg, service, root = stack
        service.render(project)
        encode_count_1 = len([c for c in ffmpeg.commands if "-filter_complex_script" in c])
        assert encode_count_1 == 2
        ffmpeg.commands.clear()
        service.render(project)  # 二次导出：片段全命中
        encode_count_2 = len([c for c in ffmpeg.commands if "-filter_complex_script" in c])
        assert encode_count_2 == 0
        assert any("probe(clip_" in step for step in ffmpeg.steps)

    def test_invalid_cached_clip_rerendered(self, stack) -> None:
        manager, scene_service, project, ffmpeg, service, root = stack
        service.render(project)
        ffmpeg.commands.clear()
        rerendered = {"n": 0}

        original_run = ffmpeg.run

        def counting_run(args, **kwargs):
            if "-filter_complex_script" in args:
                rerendered["n"] += 1
            return original_run(args, **kwargs)

        ffmpeg.run = counting_run  # type: ignore[method-assign]

        def probe_side(path):
            name = Path(path).name
            # 已缓存片段（.mp4 结尾非 .part.mp4）验证失败 → 视为无效
            if name.startswith("clip_") and not name.endswith(".part.mp4"):
                # 重渲染产物（曾以 .part.mp4 写出后改名）无法区分：
                # 用宽度标记——首次探测返回无效，其后有效
                if rerendered["n"] == 0:
                    return good_probe(width=999, duration_s=2.0)
            if name.startswith(("concat", "final_video")):
                return good_probe(duration_s=4.0)
            return good_probe(duration_s=2.0)

        ffmpeg.probe = probe_side  # type: ignore[method-assign]
        service.render(project)
        assert rerendered["n"] >= 1  # 无效缓存触发了重渲染，而非直接复用


class TestStagingTransaction:
    def test_call_sequence(self, stack) -> None:
        """测试 10：预检 → SRT → 片段 → concat → remux → 输出。"""
        manager, scene_service, project, ffmpeg, service, root = stack
        service.render(project)
        step_names = [s for s in ffmpeg.steps if not s.startswith("probe")]
        assert step_names[0].startswith("capabilities")
        render_steps = [s for s in step_names if s.startswith("渲染场景")]
        assert len(render_steps) == 2
        assert step_names.index("合并场景") > step_names.index(render_steps[-1])
        assert step_names.index("写入输出") > step_names.index("合并场景")
        # 三件套落盘
        assert (root / "output" / "final_video.mp4").is_file()
        assert (root / "output" / "subtitles.srt").is_file()
        assert (root / "output" / "credits.txt").is_file()
        # 引用与保存
        assert project.output["video_path"] == "output/final_video.mp4"
        assert project.output["subtitle_path"] == "subtitles/subtitles.srt"
        assert not scene_service.is_dirty  # 已保存
        # 无 staging 残留、无 .part.mp4 残留
        assert not list((root / "temp").glob("export_*"))
        assert not list(root.rglob("*.part.mp4"))

    def test_failure_keeps_previous_output(self, stack) -> None:
        """测试 11：失败保留上一次成功输出、Project 不被写。"""
        manager, scene_service, project, ffmpeg, service, root = stack
        service.render(project)  # 第一次成功
        previous = (root / "output" / "final_video.mp4").read_bytes()
        # 修改场景使缓存失效 → 需要重渲染 → 令 concat 失败
        scene_service.update_scene_text(project, 0, "新的第一场景文字。")
        # 补配音（文字修改清掉了）
        audio = root / "audio" / "tts_new.mp3"
        audio.write_bytes(b"new audio")
        scene_service.set_scene_audio(project, 0, "audio/tts_new.mp3", 2.0)
        ffmpeg.fail_on_step = "合并场景"
        with pytest.raises(Exception, match="合并场景"):
            service.render(project)
        # 上一次输出保留、引用未被写、无 staging 残留
        assert (root / "output" / "final_video.mp4").read_bytes() == previous
        assert project.output["video_path"] is None  # 失效后未被重写
        assert not list((root / "temp").glob("export_*"))

    def test_save_failure_keeps_video_and_reports(self, stack, monkeypatch) -> None:
        """测试 12：视频已生成但 project.json 保存失败。"""
        from auto_video_maker.services.scene_service import SceneServiceError
        from auto_video_maker.services.video_render_service import (
            ProjectSaveAfterRenderError,
        )

        manager, scene_service, project, ffmpeg, service, root = stack
        monkeypatch.setattr(
            scene_service, "save",
            lambda p: (_ for _ in ()).throw(SceneServiceError("磁盘只读")),
        )
        with pytest.raises(ProjectSaveAfterRenderError, match="视频已生成"):
            service.render(project)
        assert (root / "output" / "final_video.mp4").is_file()  # 视频保留


class TestFinalValidation:
    def test_bad_final_duration_rejected(self, stack) -> None:
        manager, scene_service, project, ffmpeg, service, root = stack
        original_probe = ffmpeg.probe

        def probe_side(path):
            name = Path(path).name
            if name == "final_video.part.mp4":
                return good_probe(duration_s=1.0)  # 总时长应为 4.0
            return good_probe(duration_s=2.0)

        ffmpeg.probe = probe_side  # type: ignore[method-assign]
        with pytest.raises(Exception, match="duration_out_of_range"):
            service.render(project)
        # 失败未污染 output/
        assert not (root / "output" / "final_video.mp4").exists()


class TestConcatListSafety:
    def test_special_char_paths(self, tmp_path) -> None:
        """测试 6：中文、空格、单引号路径的 concat 列表。"""
        line = concat_list_line(tmp_path / "我的 视频's" / "clip.mp4")
        assert line.startswith("file '")
        assert "我的 视频" in line
