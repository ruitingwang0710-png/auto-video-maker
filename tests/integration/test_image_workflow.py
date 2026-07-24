"""集成测试：关键词 → 搜索 → 下载 → 写入场景 → 保存 → 重开。

全部使用 MockTransport / Fake，不发真实网络请求。
"""

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from auto_video_maker.providers.image_provider import OpenverseImageProvider
from auto_video_maker.services.asset_download_service import (
    AssetDownloadNetworkError,
    AssetDownloadService,
)
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.scene_splitter import RuleBasedSceneSplitter

SCRIPT = "悉尼歌剧院坐落在美丽的海边。白色的帆形屋顶在阳光下闪闪发光。"

OPENVERSE_RESULT = {
    "id": "opera-1",
    "title": "Sydney Opera House",
    "source": "wikimedia",
    "thumbnail": "https://img.example.com/thumb.jpg",
    "url": "https://img.example.com/full.jpg",
    "foreign_landing_url": "https://commons.example.com/p/opera-1",
    "creator": "Example Author",
    "creator_url": "https://example.com/author",
    "license": "by",
    "license_version": "4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "attribution": "Sydney Opera House by Example Author, CC BY 4.0",
    "width": 1920,
    "height": 1080,
}


def image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color=(10, 60, 120)).save(buffer, format="JPEG")
    return buffer.getvalue()


def make_handler():
    payload = image_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.openverse.org":
            return httpx.Response(200, json={"results": [OPENVERSE_RESULT]})
        if host == "img.example.com":
            return httpx.Response(200, content=payload,
                                  headers={"Content-Type": "image/jpeg"})
        return httpx.Response(404)

    return handler


def test_full_image_workflow(tmp_path: Path) -> None:
    transport = httpx.MockTransport(make_handler())
    manager = ProjectManager()
    scene_service = SceneService(RuleBasedSceneSplitter(), manager)
    provider = OpenverseImageProvider(transport=transport, sleeper=lambda s: None)
    downloader = AssetDownloadService(transport=transport, sleeper=lambda s: None)

    # 建项目并拆分
    project = manager.create_project("图片流程", SCRIPT, "9:16", tmp_path / "out")
    scene_service.split_script(project)
    scene_service.save(project)
    project_root = manager.project_directory(project)

    # 关键词（手动编辑路径，不依赖 LLM）
    scene_service.set_scene_keywords(project, 0, ["Sydney Opera House"])

    # 搜索候选
    candidates = provider.search("Sydney Opera House")
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.license == "by"

    # 下载并写入场景
    asset = downloader.download(candidate, project_root)
    scene_service.set_scene_asset(project, 0, asset)
    scene_service.save(project)

    # local_path 相对且文件存在
    stored = project.scenes[0].selected_asset
    assert stored["local_path"] == "assets/openverse_opera-1.jpg"
    assert not Path(stored["local_path"]).is_absolute()
    assert (project_root / stored["local_path"]).is_file()

    # project.json 中无绝对路径
    raw = (project_root / "project.json").read_text(encoding="utf-8")
    assert str(project_root) not in raw.replace(str(project_root.name), "")
    assert '"local_path": "assets/openverse_opera-1.jpg"' in raw

    # 重开：版权元数据完整（测试要求 12）
    reloaded = manager.load_project(project_root)
    stored = reloaded.scenes[0].selected_asset
    assert stored["provider"] == "openverse"
    assert stored["source"] == "wikimedia"
    assert stored["title"] == "Sydney Opera House"
    assert stored["author"] == "Example Author"
    assert stored["author_url"] == "https://example.com/author"
    assert stored["license"] == "by"
    assert stored["license_version"] == "4.0"
    assert stored["license_url"] == OPENVERSE_RESULT["license_url"]
    assert stored["attribution"] == OPENVERSE_RESULT["attribution"]
    assert stored["source_page"] == OPENVERSE_RESULT["foreign_landing_url"]
    assert stored["width"] == 64 and stored["height"] == 48
    assert reloaded.scenes[0].search_keywords == ["Sydney Opera House"]


def test_offline_search_does_not_lose_project(tmp_path: Path) -> None:
    """断网：搜索失败但项目数据不丢失。"""

    def offline_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    transport = httpx.MockTransport(offline_handler)
    manager = ProjectManager()
    scene_service = SceneService(RuleBasedSceneSplitter(), manager)
    provider = OpenverseImageProvider(
        transport=transport, sleeper=lambda s: None, max_retries=1
    )

    project = manager.create_project("断网测试", SCRIPT, "9:16", tmp_path / "out")
    scene_service.split_script(project)
    scene_service.save(project)
    project_root = manager.project_directory(project)
    saved = (project_root / "project.json").read_text(encoding="utf-8")

    from auto_video_maker.providers.image_provider import ImageProviderNetworkError

    with pytest.raises(ImageProviderNetworkError):
        provider.search("Sydney Opera House")

    # 项目文件未变、内存数据未变
    assert (project_root / "project.json").read_text(encoding="utf-8") == saved
    assert project.scenes[0].selected_asset is None
    assert not scene_service.is_dirty


def test_offline_download_does_not_lose_project(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openverse.org":
            return httpx.Response(200, json={"results": [OPENVERSE_RESULT]})
        raise httpx.ConnectError("offline")

    transport = httpx.MockTransport(handler)
    manager = ProjectManager()
    scene_service = SceneService(RuleBasedSceneSplitter(), manager)
    provider = OpenverseImageProvider(transport=transport, sleeper=lambda s: None)
    downloader = AssetDownloadService(
        transport=transport, sleeper=lambda s: None, max_retries=1
    )

    project = manager.create_project("下载断网", SCRIPT, "9:16", tmp_path / "out")
    scene_service.split_script(project)
    scene_service.save(project)
    project_root = manager.project_directory(project)

    candidate = provider.search("opera")[0]
    with pytest.raises(AssetDownloadNetworkError):
        downloader.download(candidate, project_root)

    assert project.scenes[0].selected_asset is None
    assert not list((project_root / "assets").glob("*.part"))
