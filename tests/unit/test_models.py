"""数据模型单元测试。"""

import pytest

from auto_video_maker.models.project import (
    ASPECT_RATIO_RESOLUTIONS,
    PROJECT_VERSION,
    Project,
    ProjectSettings,
    Scene,
)


def make_project() -> Project:
    settings = ProjectSettings(
        aspect_ratio="9:16",
        resolution="1080x1920",
        voice="default",
        subtitle_enabled=True,
        output_directory="/tmp/输出",
    )
    return Project(
        project_name="测试项目",
        original_script="人工智能正在改变企业的工作方式。",
        settings=settings,
        scenes=[
            Scene(scene_id=1, text="第一场景", search_keywords=["ai office"]),
        ],
    )


def test_project_defaults() -> None:
    project = make_project()
    assert project.project_version == PROJECT_VERSION == "0.1"
    assert project.project_id
    assert project.created_at
    assert project.updated_at
    assert project.output == {"video_path": None, "subtitle_path": None, "status": "draft"}


def test_aspect_ratio_mapping() -> None:
    assert ASPECT_RATIO_RESOLUTIONS["9:16"] == "1080x1920"
    assert ASPECT_RATIO_RESOLUTIONS["16:9"] == "1920x1080"


def test_project_dict_roundtrip() -> None:
    project = make_project()
    data = project.to_dict()
    restored = Project.from_dict(data)
    assert restored.to_dict() == data


def test_project_dict_contains_required_keys() -> None:
    data = make_project().to_dict()
    for key in Project.REQUIRED_KEYS:
        assert key in data
    settings = data["settings"]
    for key in ("aspect_ratio", "resolution", "voice", "subtitle_enabled", "output_directory"):
        assert key in settings
    scene = data["scenes"][0]
    for key in ("scene_id", "text", "search_keywords", "selected_asset", "audio_path", "duration", "status"):
        assert key in scene


def test_from_dict_missing_key_raises() -> None:
    data = make_project().to_dict()
    del data["original_script"]
    with pytest.raises(ValueError, match="original_script"):
        Project.from_dict(data)


def test_scene_defaults() -> None:
    scene = Scene(scene_id=1, text="文字")
    assert scene.status == "pending"
    assert scene.search_keywords == []
    assert scene.selected_asset is None
    assert scene.audio_path is None
    assert scene.duration is None
