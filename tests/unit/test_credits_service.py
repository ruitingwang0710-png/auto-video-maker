"""CreditsService 测试（注入时间源，不依赖真实时间）。"""

from pathlib import Path

from auto_video_maker.models.project import Project, ProjectSettings, Scene
from auto_video_maker.services.credits_service import CreditsService

FIXED_TIME = "2026-07-25T10:00:00+10:00"


def make_project() -> Project:
    return Project(
        project_name="致谢测试",
        original_script="脚本",
        settings=ProjectSettings(output_directory="/tmp/x"),
        scenes=[
            Scene(scene_id=1, text="场景一", selected_asset={
                "provider": "openverse", "source": "wikimedia",
                "asset_id": "a1", "title": "Opera House",
                "local_path": "assets/a1.jpg",
                "source_page": "https://commons.example.com/a1",
                "author": "Alice", "author_url": "https://example.com/alice",
                "license": "by", "license_version": "4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "attribution": "Opera House by Alice, CC BY 4.0",
                "width": 100, "height": 100,
            }),
            Scene(scene_id=2, text="场景二", selected_asset={
                "provider": "local", "source": "local", "asset_id": "h",
                "title": "我的照片.png", "local_path": "assets/local_h.png",
                "source_page": "", "author": "", "author_url": "",
                "license": "user-provided", "license_version": "",
                "license_url": "", "attribution": "", "width": 1, "height": 1,
            }),
        ],
    )


def make_service() -> CreditsService:
    return CreditsService(timestamp_provider=lambda: FIXED_TIME)


def test_header_uses_injected_timestamp() -> None:
    text = make_service().build_text(make_project())
    assert "项目：致谢测试" in text
    assert f"生成时间：{FIXED_TIME}" in text  # ISO 8601，可预期


def test_openverse_asset_fields_complete() -> None:
    text = make_service().build_text(make_project())
    assert "场景 1：" in text
    assert "Opera House" in text
    assert "Alice" in text and "https://example.com/alice" in text
    assert "https://commons.example.com/a1" in text
    assert "BY 4.0" in text
    assert "https://creativecommons.org/licenses/by/4.0/" in text
    assert "Opera House by Alice, CC BY 4.0" in text


def test_local_asset_marked() -> None:
    text = make_service().build_text(make_project())
    assert "场景 2：" in text
    assert "用户提供的本地图片" in text


def test_generate_writes_utf8(tmp_path: Path) -> None:
    target = tmp_path / "staging" / "credits.part.txt"
    make_service().generate(make_project(), target)
    content = target.read_text(encoding="utf-8")
    assert "致谢测试" in content
