"""校验工具单元测试。"""

from pathlib import Path

import pytest

from auto_video_maker.utils.validation import (
    ValidationError,
    ensure_within_directory,
    ensure_writable_directory,
    validate_project_name,
)


def test_valid_names() -> None:
    assert validate_project_name("我的项目") == "我的项目"
    assert validate_project_name("  demo 01  ") == "demo 01"
    assert validate_project_name("视频-2026_v1") == "视频-2026_v1"


@pytest.mark.parametrize("name", ["", " ", "a/b", "a\\b", "..", "x..y", "CON", "lpt3", "a\nb", "."])
def test_invalid_names(name: str) -> None:
    with pytest.raises(ValidationError):
        validate_project_name(name)


def test_ensure_writable_creates_directory(tmp_path: Path) -> None:
    target = tmp_path / "新建" / "子目录"
    resolved = ensure_writable_directory(target)
    assert resolved.is_dir()


def test_ensure_within_directory_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ensure_within_directory(tmp_path / ".." / "escape", tmp_path)
    with pytest.raises(ValidationError):
        ensure_within_directory(tmp_path, tmp_path)
