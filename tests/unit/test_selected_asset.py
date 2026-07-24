"""SelectedAsset 模型测试：字段验证、路径安全、序列化与兼容。"""

import os
from pathlib import Path

import pytest

from auto_video_maker.models.project import Scene
from auto_video_maker.models.selected_asset import (
    AssetValidationError,
    SelectedAsset,
)


def valid_kwargs(**overrides) -> dict:
    data = {
        "provider": "openverse",
        "source": "wikimedia",
        "asset_id": "abc-123",
        "title": "Sydney Opera House",
        "local_path": "assets/openverse_abc-123.jpg",
        "source_page": "https://example.com/photo/abc-123",
        "author": "Example Author",
        "author_url": "https://example.com/author",
        "license": "by",
        "license_version": "4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Photo by Example Author, CC BY 4.0",
        "width": 1920,
        "height": 1080,
    }
    data.update(overrides)
    return data


class TestValidation:
    def test_valid_asset(self) -> None:
        asset = SelectedAsset(**valid_kwargs())
        assert asset.provider == "openverse"
        assert asset.width == 1920

    @pytest.mark.parametrize("field", ["provider", "asset_id", "local_path", "license"])
    def test_required_fields_non_empty(self, field: str) -> None:
        with pytest.raises(AssetValidationError):
            SelectedAsset(**valid_kwargs(**{field: "  "}))

    def test_optional_fields_may_be_empty(self) -> None:
        asset = SelectedAsset(
            **valid_kwargs(source="", title="", source_page="", author="",
                           author_url="", license_version="", license_url="",
                           attribution="", width=None, height=None)
        )
        assert asset.width is None

    def test_non_string_field_rejected(self) -> None:
        with pytest.raises(AssetValidationError):
            SelectedAsset(**valid_kwargs(author=123))

    def test_bad_dimensions_rejected(self) -> None:
        with pytest.raises(AssetValidationError):
            SelectedAsset(**valid_kwargs(width="wide"))


class TestPathSafety:
    @pytest.mark.parametrize("bad_path", [
        "/absolute/path.jpg",
        "../outside.jpg",
        "assets/../../outside.jpg",
        "a\\b.jpg",
        "  ",
    ])
    def test_escaping_paths_rejected(self, bad_path: str) -> None:
        with pytest.raises(AssetValidationError):
            SelectedAsset(**valid_kwargs(local_path=bad_path))

    def test_resolve_within_project(self, tmp_path: Path) -> None:
        (tmp_path / "assets").mkdir()
        asset = SelectedAsset(**valid_kwargs())
        resolved = asset.resolve_within(tmp_path)
        assert resolved.is_relative_to(tmp_path.resolve())

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        project = tmp_path / "project"
        (project / "assets").mkdir(parents=True)
        os.symlink(outside, project / "assets" / "link")
        asset = SelectedAsset(**valid_kwargs(local_path="assets/link/pic.jpg"))
        with pytest.raises(AssetValidationError, match="项目目录之外"):
            asset.resolve_within(project)


class TestSerialization:
    def test_roundtrip(self) -> None:
        asset = SelectedAsset(**valid_kwargs())
        assert SelectedAsset.from_dict(asset.to_dict()) == asset

    def test_from_dict_missing_field_rejected(self) -> None:
        data = valid_kwargs()
        del data["license_url"]
        with pytest.raises(AssetValidationError, match="license_url"):
            SelectedAsset.from_dict(data)

    def test_from_dict_ignores_unknown_keys(self) -> None:
        data = valid_kwargs()
        data["unknown_key"] = "x"
        assert SelectedAsset.from_dict(data).asset_id == "abc-123"


class TestStorageCompatibility:
    def test_null_stays_null(self) -> None:
        assert SelectedAsset.from_storage(None) is None
        assert SelectedAsset.from_storage("oops") is None

    def test_valid_dict_normalized(self) -> None:
        stored = SelectedAsset.from_storage(valid_kwargs())
        assert stored is not None
        assert stored["provider"] == "openverse"
        assert set(stored) == set(valid_kwargs())

    def test_missing_optional_fields_filled(self) -> None:
        data = valid_kwargs()
        del data["attribution"]
        del data["width"]
        stored = SelectedAsset.from_storage(data)
        assert stored["attribution"] == ""
        assert stored["width"] is None

    def test_unrepairable_data_preserved_not_raised(self) -> None:
        # 缺核心字段的旧数据：不抛异常、原样保留（保证项目能打开）
        stored = SelectedAsset.from_storage({"provider": "openverse"})
        assert stored == {"provider": "openverse"}

    def test_scene_from_dict_with_null_asset(self) -> None:
        """测试要求 16（模型层）：selected_asset=null 的旧场景正常加载。"""
        scene = Scene.from_dict(
            {"scene_id": 1, "text": "旧场景", "selected_asset": None}
        )
        assert scene.selected_asset is None

    def test_scene_from_dict_with_asset(self) -> None:
        scene = Scene.from_dict(
            {"scene_id": 1, "text": "场景", "selected_asset": valid_kwargs()}
        )
        assert scene.selected_asset["asset_id"] == "abc-123"
