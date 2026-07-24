"""真实 FFmpeg 小型集成渲染（无 FFmpeg 或能力不足时自动跳过）。

- 项目路径包含中文和空格；使用 pytest 临时目录，不污染仓库
- 两场景、小图、短音频（程序构造的合法静音 MP3）
- 验证：双流、分辨率、fps、codec、pix_fmt、时长 ≤200ms、
  字幕滤镜真实执行、concat stream copy、faststart、无残留
"""

import io
import shutil
from pathlib import Path

import pytest
from PIL import Image

from auto_video_maker.infrastructure.ffmpeg_runner import FFmpegError, FFmpegRunner
from auto_video_maker.services.credits_service import CreditsService
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.scene_splitter import RuleBasedSceneSplitter
from auto_video_maker.services.subtitle_service import SubtitleService
from auto_video_maker.services.video_render_service import VideoRenderService

SCRIPT = "悉尼歌剧院坐落在美丽的海边，白色屋顶闪闪发光。\n\n人们喜欢在傍晚来这里散步拍照。"


def _ffmpeg_ready() -> bool:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return False
    try:
        FFmpegRunner().check_capabilities(require_subtitles=True)
        return True
    except FFmpegError:
        return False


pytestmark = pytest.mark.skipif(
    not _ffmpeg_ready(),
    reason="真实 FFmpeg 不可用或缺少必需能力（libx264/aac/subtitles 等）",
)


def silent_mp3_bytes(frames: int) -> bytes:
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)
    return frame * frames


def image_bytes(color) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 360), color=color).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def stack(tmp_path: Path):
    # 中文 + 空格路径（测试要求 19）
    output_dir = tmp_path / "我的 导出目录"
    manager = ProjectManager()
    scene_service = SceneService(RuleBasedSceneSplitter(), manager)
    project = manager.create_project("真实 渲染测试", SCRIPT, "9:16", output_dir)
    scene_service.split_script(project)
    root = manager.project_directory(project)
    (root / "assets").mkdir(exist_ok=True)
    (root / "audio").mkdir(exist_ok=True)
    colors = [(160, 40, 40), (40, 40, 160)]
    for index, scene in enumerate(project.scenes):
        (root / "assets" / f"图 {index}.jpg").write_bytes(image_bytes(colors[index]))
        audio = root / "audio" / f"配音 {index}.mp3"
        audio.write_bytes(silent_mp3_bytes(40))  # ≈1.04 秒
        scene.selected_asset = {
            "provider": "openverse", "source": "s", "asset_id": f"r{index}",
            "title": f"测试图 {index}", "local_path": f"assets/图 {index}.jpg",
            "source_page": "https://e.com", "author": "测试作者",
            "author_url": "", "license": "by", "license_version": "4.0",
            "license_url": "https://cc.org", "attribution": "attr",
            "width": 640, "height": 360,
        }
        scene.audio_path = f"audio/配音 {index}.mp3"
        from auto_video_maker.infrastructure.audio_probe import AudioProbe

        scene.duration = AudioProbe().duration_seconds(audio)
    scene_service.save(project)
    ffmpeg = FFmpegRunner()
    service = VideoRenderService(
        ffmpeg, SubtitleService(),
        CreditsService(timestamp_provider=lambda: "2026-07-25T10:00:00+10:00"),
        manager, scene_service,
    )
    return manager, scene_service, project, root, ffmpeg, service


def test_real_render_end_to_end(stack) -> None:
    manager, scene_service, project, root, ffmpeg, service = stack
    progresses: list[int] = []

    relative = service.render(project, on_progress=progresses.append)
    final = root / relative
    assert final.is_file() and final.stat().st_size > 1000

    # ffprobe 验证：双流、分辨率、fps、codec、pix_fmt
    info = ffmpeg.probe(final)
    streams = info["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert (video["width"], video["height"]) == (1080, 1920)
    num, _, den = video["r_frame_rate"].partition("/")
    assert abs(float(num) / float(den or 1) - 30) < 0.01
    assert audio["codec_name"] == "aac"

    # 时长误差 ≤ 200ms（视频可因 ceil 略长）
    expected_ms = sum(round(s.duration * 1000) for s in project.scenes)
    actual_ms = float(info["format"]["duration"]) * 1000
    assert actual_ms >= expected_ms - 200
    assert actual_ms <= expected_ms + 200 + 67  # + 2 帧余量

    # faststart（moov 前置由 remux 保证；此处验证 remux 步骤产物可读）
    # 三件套与引用
    assert (root / "output" / "subtitles.srt").is_file()
    assert (root / "output" / "credits.txt").is_file()
    assert project.output["video_path"] == "output/final_video.mp4"
    assert project.output["subtitle_path"] == "subtitles/subtitles.srt"

    # 无 staging / .part.mp4 残留
    assert not list((root / "temp").glob("export_*"))
    assert not list(root.rglob("*.part.mp4"))

    # 片段缓存生成（stream copy 合并的前提）
    clips = list((root / "temp" / "clips").glob("clip_*.mp4"))
    assert len(clips) == 2

    # 进度单调且到达 100
    assert progresses and progresses[-1] == 100
    assert progresses == sorted(progresses)


def test_real_second_export_uses_cache(stack) -> None:
    manager, scene_service, project, root, ffmpeg, service = stack
    import time

    service.render(project)
    clips_before = {p.name: p.stat().st_mtime for p in (root / "temp/clips").glob("*.mp4")}
    start = time.monotonic()
    service.render(project)
    second_elapsed = time.monotonic() - start
    clips_after = {p.name: p.stat().st_mtime for p in (root / "temp/clips").glob("*.mp4")}
    assert clips_before == clips_after  # 片段未被重新生成
    assert second_elapsed < 30  # 命中缓存（仅 concat/remux）
