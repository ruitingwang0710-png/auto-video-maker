"""集成测试：Fake 全流程导出（校验 → SRT → 片段 → concat → 输出 → 保存）。"""

import io
from pathlib import Path

import pytest
from PIL import Image

from auto_video_maker.services.credits_service import CreditsService
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.scene_splitter import RuleBasedSceneSplitter
from auto_video_maker.services.subtitle_service import SubtitleService
from auto_video_maker.services.video_render_service import VideoRenderService

from tests.unit.test_video_render_service import FakeFFmpegRunner, good_probe

SCRIPT = "第一段场景的完整文字内容，用来测试导出。\n\n第二段场景的完整文字内容，也在这里。"


def image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color=(50, 90, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


def build_project(tmp_path: Path):
    manager = ProjectManager()
    scene_service = SceneService(RuleBasedSceneSplitter(), manager)
    project = manager.create_project("集成导出", SCRIPT, "9:16", tmp_path / "out")
    scene_service.split_script(project)
    root = manager.project_directory(project)
    (root / "assets").mkdir(exist_ok=True)
    (root / "audio").mkdir(exist_ok=True)
    for index, scene in enumerate(project.scenes):
        (root / "assets" / f"i{index}.jpg").write_bytes(image_bytes())
        (root / "audio" / f"a{index}.mp3").write_bytes(b"audio%d" % index)
        scene.selected_asset = {
            "provider": "openverse", "source": "s", "asset_id": f"x{index}",
            "title": f"图 {index}", "local_path": f"assets/i{index}.jpg",
            "source_page": "https://e.com", "author": "作者",
            "author_url": "", "license": "by", "license_version": "4.0",
            "license_url": "https://cc.org", "attribution": "attr",
            "width": 64, "height": 48,
        }
        scene.audio_path = f"audio/a{index}.mp3"
        scene.duration = 1.5
    scene_service.save(project)
    return manager, scene_service, project, root


def test_full_export_workflow_fake(tmp_path: Path) -> None:
    manager, scene_service, project, root = build_project(tmp_path)
    ffmpeg = FakeFFmpegRunner()
    ffmpeg.clip_duration_s = 1.5
    ffmpeg.total_duration_s = 3.0
    service = VideoRenderService(
        ffmpeg, SubtitleService(),
        CreditsService(timestamp_provider=lambda: "2026-07-25T10:00:00+10:00"),
        manager, scene_service,
    )

    relative = service.render(project)
    assert relative == "output/final_video.mp4"

    # 三件套齐备
    assert (root / "output" / "final_video.mp4").is_file()
    srt = (root / "output" / "subtitles.srt").read_text(encoding="utf-8")
    assert "第一段场景" in srt
    credits = (root / "output" / "credits.txt").read_text(encoding="utf-8")
    assert "作者" in credits and "2026-07-25T10:00:00+10:00" in credits

    # subtitle_path 恒指向标准位置（不改指 output 副本）
    assert project.output["subtitle_path"] == "subtitles/subtitles.srt"
    assert project.output["video_path"] == "output/final_video.mp4"

    # 已保存：重开项目引用完整
    reloaded = manager.load_project(root)
    assert reloaded.output["video_path"] == "output/final_video.mp4"
    assert reloaded.output["subtitle_path"] == "subtitles/subtitles.srt"

    # 无 staging / .part.mp4 残留
    assert not list((root / "temp").glob("export_*"))
    assert not list(root.rglob("*.part.mp4"))

    # 二次导出：片段缓存命中，无重新编码
    ffmpeg.commands.clear()
    service.render(project)
    assert not any("-filter_complex_script" in c for c in ffmpeg.commands)


def test_export_after_image_change_keeps_subtitle_reference(tmp_path: Path) -> None:
    """失效矩阵端到端：换图后 video 失效、字幕引用保留、可重新导出。"""
    from auto_video_maker.models.selected_asset import SelectedAsset

    manager, scene_service, project, root = build_project(tmp_path)
    ffmpeg = FakeFFmpegRunner()
    ffmpeg.clip_duration_s = 1.5
    ffmpeg.total_duration_s = 3.0
    service = VideoRenderService(
        ffmpeg, SubtitleService(), CreditsService(lambda: "t"),
        manager, scene_service,
    )
    service.render(project)

    (root / "assets" / "new.jpg").write_bytes(image_bytes() + b"NEW")
    new_asset = SelectedAsset(
        provider="openverse", source="s", asset_id="new", title="新图",
        local_path="assets/new.jpg", source_page="", author="", author_url="",
        license="by", license_version="", license_url="", attribution="",
        width=64, height=48,
    )
    scene_service.set_scene_asset(project, 0, new_asset)
    assert project.output["video_path"] is None
    assert project.output["subtitle_path"] == "subtitles/subtitles.srt"  # 不清

    # 重新导出：仅换图场景重编码
    ffmpeg.commands.clear()
    service.render(project)
    encodes = [c for c in ffmpeg.commands if "-filter_complex_script" in c]
    assert len(encodes) == 1
    assert project.output["video_path"] == "output/final_video.mp4"
