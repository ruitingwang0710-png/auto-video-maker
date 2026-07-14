"""ProjectManager 单元测试，覆盖 TASK.md 要求的 10 项测试。"""

import json
import os
from pathlib import Path

import pytest

from auto_video_maker.services.project_manager import (
    PROJECT_FILE_NAME,
    PROJECT_SUBDIRS,
    ProjectManager,
    ProjectManagerError,
)

SCRIPT = "人工智能正在改变企业的工作方式。现在，自动化工具可以协助企业处理重复任务。"


@pytest.fixture
def manager() -> ProjectManager:
    return ProjectManager()


# 1. 创建有效项目
def test_create_valid_project(manager: ProjectManager, tmp_path: Path) -> None:
    project = manager.create_project("demo", SCRIPT, "9:16", tmp_path)
    project_dir = tmp_path / "demo"
    assert (project_dir / PROJECT_FILE_NAME).is_file()
    for subdir in PROJECT_SUBDIRS:
        assert (project_dir / subdir).is_dir()
    assert project.settings.resolution == "1080x1920"
    assert project.original_script == SCRIPT


# 2. 项目名称为空
@pytest.mark.parametrize("name", ["", "   ", "\t"])
def test_empty_project_name(manager: ProjectManager, tmp_path: Path, name: str) -> None:
    with pytest.raises(ProjectManagerError, match="项目名称不能为空"):
        manager.create_project(name, SCRIPT, "9:16", tmp_path)


# 3. 文案为空
@pytest.mark.parametrize("script", ["", "   \n  "])
def test_empty_script(manager: ProjectManager, tmp_path: Path, script: str) -> None:
    with pytest.raises(ProjectManagerError, match="文案不能为空"):
        manager.create_project("demo", script, "9:16", tmp_path)


# 4. 输出目录为空
def test_empty_output_directory(manager: ProjectManager) -> None:
    with pytest.raises(ProjectManagerError, match="输出目录不能为空"):
        manager.create_project("demo", SCRIPT, "9:16", "")


# 5. 保存并重新读取项目
def test_save_and_reload(manager: ProjectManager, tmp_path: Path) -> None:
    project = manager.create_project("roundtrip", SCRIPT, "16:9", tmp_path)
    loaded = manager.load_project(tmp_path / "roundtrip" / PROJECT_FILE_NAME)
    assert loaded.project_id == project.project_id
    assert loaded.project_name == project.project_name
    assert loaded.original_script == SCRIPT
    assert loaded.settings.aspect_ratio == "16:9"
    assert loaded.settings.resolution == "1920x1080"
    # 也支持传项目目录
    loaded_from_dir = manager.load_project(tmp_path / "roundtrip")
    assert loaded_from_dir.project_id == project.project_id


# 6. 中文项目名称
def test_chinese_project_name(manager: ProjectManager, tmp_path: Path) -> None:
    project = manager.create_project("我的视频项目", SCRIPT, "9:16", tmp_path)
    assert (tmp_path / "我的视频项目" / PROJECT_FILE_NAME).is_file()
    loaded = manager.load_project(tmp_path / "我的视频项目")
    assert loaded.project_name == "我的视频项目"
    assert loaded.project_id == project.project_id


# 7. 中文路径
def test_chinese_output_path(manager: ProjectManager, tmp_path: Path) -> None:
    output_dir = tmp_path / "桌面" / "我的视频输出"
    manager.create_project("项目一", SCRIPT, "9:16", output_dir)
    loaded = manager.load_project(output_dir / "项目一")
    assert loaded.original_script == SCRIPT


# 8. 非法项目名称
@pytest.mark.parametrize(
    "name",
    ["a/b", "a\\b", "..", "有../点", "bad\x01name", "CON", "com1", "."],
)
def test_illegal_project_names(manager: ProjectManager, tmp_path: Path, name: str) -> None:
    with pytest.raises(ProjectManagerError):
        manager.create_project(name, SCRIPT, "9:16", tmp_path)


# 9. 无写入权限目录
def test_unwritable_output_directory(manager: ProjectManager, tmp_path: Path) -> None:
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_dir.chmod(0o500)
    try:
        if os.access(readonly_dir, os.W_OK):
            pytest.skip("当前用户拥有绕过权限的能力（例如 root），跳过该测试")
        with pytest.raises(ProjectManagerError, match="不可写|无法创建"):
            manager.create_project("demo", SCRIPT, "9:16", readonly_dir)
    finally:
        readonly_dir.chmod(0o700)


# 10. project.json 内容完整
def test_project_json_content_complete(manager: ProjectManager, tmp_path: Path) -> None:
    manager.create_project("完整性检查", SCRIPT, "9:16", tmp_path)
    project_file = tmp_path / "完整性检查" / PROJECT_FILE_NAME
    raw = project_file.read_text(encoding="utf-8")
    # 中文必须原样保存，不转义为 \uXXXX
    assert "人工智能" in raw
    assert "完整性检查" in raw
    data = json.loads(raw)
    for key in (
        "project_version",
        "project_id",
        "project_name",
        "created_at",
        "updated_at",
        "original_script",
        "settings",
        "scenes",
        "output",
    ):
        assert key in data, f"project.json 缺少字段 {key}"
    assert data["project_version"] == "0.1"
    for key in ("aspect_ratio", "resolution", "voice", "subtitle_enabled", "output_directory"):
        assert key in data["settings"]
    assert data["output"] == {"video_path": None, "subtitle_path": None, "status": "draft"}
    # 时间为 ISO 8601
    assert "T" in data["created_at"]


# 其他健壮性
def test_duplicate_project_rejected(manager: ProjectManager, tmp_path: Path) -> None:
    manager.create_project("dup", SCRIPT, "9:16", tmp_path)
    with pytest.raises(ProjectManagerError, match="已存在项目"):
        manager.create_project("dup", SCRIPT, "9:16", tmp_path)


def test_invalid_aspect_ratio(manager: ProjectManager, tmp_path: Path) -> None:
    with pytest.raises(ProjectManagerError, match="不支持的视频比例"):
        manager.create_project("demo", SCRIPT, "4:3", tmp_path)


def test_load_missing_project(manager: ProjectManager, tmp_path: Path) -> None:
    with pytest.raises(ProjectManagerError, match="找不到项目文件"):
        manager.load_project(tmp_path / "不存在" / PROJECT_FILE_NAME)


def test_load_corrupted_project(manager: ProjectManager, tmp_path: Path) -> None:
    bad = tmp_path / PROJECT_FILE_NAME
    bad.write_text("{ 不是合法 JSON", encoding="utf-8")
    with pytest.raises(ProjectManagerError, match="损坏"):
        manager.load_project(bad)
