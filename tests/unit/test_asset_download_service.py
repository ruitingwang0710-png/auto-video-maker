"""AssetDownloadService 测试（httpx.MockTransport + Pillow 生成的测试图片）。"""

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

import auto_video_maker.services.asset_download_service as ads
from auto_video_maker.models.selected_asset import SelectedAsset
from auto_video_maker.providers.image_provider import ImageCandidate
from auto_video_maker.services.asset_download_service import (
    AssetDownloadError,
    AssetDownloadNetworkError,
    AssetDownloadService,
)


def image_bytes(fmt: str = "JPEG", size: tuple[int, int] = (32, 24)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(120, 30, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


def make_candidate(**overrides) -> ImageCandidate:
    data = dict(
        provider="openverse",
        source="wikimedia",
        asset_id="abc-123",
        title="Sydney Opera House",
        preview_url="https://img.example.com/thumb.jpg",
        download_url="https://img.example.com/full.jpg",
        source_page="https://commons.example.com/p/abc",
        author="Author",
        author_url="https://example.com/a",
        license="by",
        license_version="4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Photo by Author, CC BY 4.0",
        width=1920,
        height=1080,
    )
    data.update(overrides)
    return ImageCandidate(**data)


def make_service(handler, max_retries: int = 2) -> AssetDownloadService:
    return AssetDownloadService(
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
        sleeper=lambda s: None,
    )


class TestDownload:
    def test_successful_download(self, tmp_path: Path) -> None:
        payload = image_bytes("JPEG")

        service = make_service(
            lambda r: httpx.Response(200, content=payload,
                                     headers={"Content-Type": "image/jpeg"})
        )
        asset = service.download(make_candidate(), tmp_path)
        assert isinstance(asset, SelectedAsset)
        assert asset.local_path == "assets/openverse_abc-123.jpg"
        assert (tmp_path / asset.local_path).is_file()
        assert asset.license == "by"
        assert asset.attribution == "Photo by Author, CC BY 4.0"
        assert asset.width == 32 and asset.height == 24  # 实际尺寸
        # 无 .part 残留
        assert not list((tmp_path / "assets").glob("*.part"))

    def test_png_extension_by_actual_format(self, tmp_path: Path) -> None:
        payload = image_bytes("PNG")
        service = make_service(
            lambda r: httpx.Response(200, content=payload,
                                     headers={"Content-Type": "application/octet-stream"})
        )
        asset = service.download(make_candidate(), tmp_path)
        assert asset.local_path.endswith(".png")  # 按实际格式，不信 Content-Type

    def test_spoofed_content_type_rejected_no_retry(self, tmp_path: Path) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, content=b"not an image at all",
                                  headers={"Content-Type": "image/jpeg"})

        service = make_service(handler, max_retries=3)
        with pytest.raises(AssetDownloadError, match="损坏|不是有效"):
            service.download(make_candidate(), tmp_path)
        assert len(calls) == 1  # 格式错误不重试
        assert not list((tmp_path / "assets").iterdir())  # 无残留

    def test_gif_rejected(self, tmp_path: Path) -> None:
        payload = image_bytes("GIF")
        service = make_service(lambda r: httpx.Response(200, content=payload))
        with pytest.raises(AssetDownloadError, match="不支持的图片格式"):
            service.download(make_candidate(), tmp_path)

    def test_oversize_aborted(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(ads, "MAX_DOWNLOAD_BYTES", 100)
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, content=b"x" * 1000)

        service = make_service(handler, max_retries=3)
        with pytest.raises(AssetDownloadError, match="15MB"):
            service.download(make_candidate(), tmp_path)
        assert len(calls) == 1  # 超限不重试
        assert not list((tmp_path / "assets").iterdir())

    def test_decompression_bomb_rejected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(ads, "MAX_IMAGE_PIXELS", 100)  # 32x24=768 > 100
        service = make_service(lambda r: httpx.Response(200, content=image_bytes()))
        with pytest.raises(AssetDownloadError, match="像素规模过大"):
            service.download(make_candidate(), tmp_path)

    def test_network_error_retries_then_fails(self, tmp_path: Path) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            raise httpx.ConnectError("offline")

        service = make_service(handler, max_retries=2)
        with pytest.raises(AssetDownloadNetworkError):
            service.download(make_candidate(), tmp_path)
        assert len(calls) == 3

    def test_5xx_retries_then_succeeds(self, tmp_path: Path) -> None:
        payload = image_bytes()
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) < 2:
                return httpx.Response(503)
            return httpx.Response(200, content=payload)

        service = make_service(handler)
        asset = service.download(make_candidate(), tmp_path)
        assert asset.local_path.endswith(".jpg")
        assert len(calls) == 2

    def test_404_no_retry(self, tmp_path: Path) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(404)

        service = make_service(handler, max_retries=3)
        with pytest.raises(AssetDownloadError, match="404"):
            service.download(make_candidate(), tmp_path)
        assert len(calls) == 1


class TestCache:
    def test_valid_cache_reused_without_request(self, tmp_path: Path) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "openverse_abc-123.jpg").write_bytes(image_bytes("JPEG"))
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, content=image_bytes())

        service = make_service(handler)
        asset = service.download(make_candidate(), tmp_path)
        assert calls == []  # 缓存命中，零请求
        assert asset.local_path == "assets/openverse_abc-123.jpg"

    def test_corrupted_cache_not_reused(self, tmp_path: Path) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "openverse_abc-123.jpg").write_bytes(b"corrupted bytes")
        payload = image_bytes("JPEG")
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, content=payload)

        service = make_service(handler)
        asset = service.download(make_candidate(), tmp_path)
        assert len(calls) == 1  # 损坏缓存不复用，重新下载
        assert (tmp_path / asset.local_path).stat().st_size == len(payload)


class TestLocalImport:
    def test_import_local_file(self, tmp_path: Path) -> None:
        source = tmp_path / "我的照片.png"
        source.write_bytes(image_bytes("PNG"))
        project_root = tmp_path / "project"
        project_root.mkdir()

        service = make_service(lambda r: httpx.Response(500))
        asset = service.import_local_file(source, project_root)

        assert asset.provider == "local"
        assert asset.source == "local"
        assert asset.license == "user-provided"
        assert asset.title == "我的照片.png"
        assert len(asset.asset_id) == 16  # 内容哈希
        assert asset.local_path.startswith("assets/local_")
        assert asset.local_path.endswith(".png")
        assert (project_root / asset.local_path).is_file()
        assert source.is_file()  # 原文件保留

    def test_same_content_same_hash(self, tmp_path: Path) -> None:
        payload = image_bytes("JPEG")
        first = tmp_path / "a.jpg"
        second = tmp_path / "b.jpg"
        first.write_bytes(payload)
        second.write_bytes(payload)
        project_root = tmp_path / "project"
        project_root.mkdir()
        service = make_service(lambda r: httpx.Response(500))
        asset_a = service.import_local_file(first, project_root)
        asset_b = service.import_local_file(second, project_root)
        assert asset_a.asset_id == asset_b.asset_id
        assert asset_a.local_path == asset_b.local_path

    def test_non_image_rejected(self, tmp_path: Path) -> None:
        source = tmp_path / "not_image.jpg"
        source.write_bytes(b"just text")
        project_root = tmp_path / "project"
        project_root.mkdir()
        service = make_service(lambda r: httpx.Response(500))
        with pytest.raises(AssetDownloadError):
            service.import_local_file(source, project_root)

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        service = make_service(lambda r: httpx.Response(500))
        with pytest.raises(AssetDownloadError, match="找不到文件"):
            service.import_local_file(tmp_path / "nope.jpg", tmp_path)


class TestPreview:
    def test_fetch_preview_success(self) -> None:
        payload = image_bytes("JPEG")
        service = make_service(lambda r: httpx.Response(200, content=payload))
        assert service.fetch_preview("https://img.example.com/t.jpg") == payload

    def test_fetch_preview_failure(self) -> None:
        service = make_service(lambda r: httpx.Response(404))
        with pytest.raises(AssetDownloadError):
            service.fetch_preview("https://img.example.com/t.jpg")
