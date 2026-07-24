"""素材下载与本地导入：校验、缓存与 SelectedAsset 构造。

安全规则（见 TASK.md）：
- 单图 ≤ 15MB；仅 JPEG/PNG/WebP；实际格式校验（不信任 Content-Type）
- .part 临时文件下载，全部验证通过后原子移动
- Pillow 完整性校验 + decompression bomb 防护
- 只对网络错误、429、可恢复 5xx 重试；格式/损坏/超限不重试
- 缓存文件完整验证通过后才复用
- local_path 一律为相对项目根目录的路径
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Callable

import httpx
from PIL import Image, UnidentifiedImageError

from auto_video_maker import __version__
from auto_video_maker.models.selected_asset import (
    LOCAL_PROVIDER,
    USER_PROVIDED_LICENSE,
    SelectedAsset,
)
from auto_video_maker.providers.image_provider import ImageCandidate

logger = logging.getLogger(__name__)

USER_AGENT = f"AutoVideoMaker/{__version__}"
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024  # 15MB
MAX_IMAGE_PIXELS = 50_000_000  # decompression bomb 防护
ASSETS_DIR_NAME = "assets"

_FORMAT_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_SAFE_ID_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")

# 全局兜底：防止 Pillow 在 open 阶段解码超大图
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class AssetDownloadError(Exception):
    """素材下载/导入失败（不可重试类）。消息面向用户。"""


class AssetDownloadNetworkError(AssetDownloadError):
    """网络失败（重试耗尽后抛出）。"""


def _safe_id(asset_id: str) -> str:
    cleaned = _SAFE_ID_PATTERN.sub("_", asset_id).strip("._")
    return cleaned[:80] or "asset"


def _validate_image_file(path: Path) -> tuple[str, int, int]:
    """校验实际图片格式与完整性，返回 (format, width, height)。

    失败抛出 AssetDownloadError（不可重试）。
    """
    if path.stat().st_size > MAX_DOWNLOAD_BYTES:
        raise AssetDownloadError("图片超过 15MB 大小限制。")
    try:
        with Image.open(path) as image:
            image_format = image.format or ""
            width, height = image.size
            if image_format not in _FORMAT_EXTENSIONS:
                raise AssetDownloadError(
                    f"不支持的图片格式：{image_format or '未知'}。"
                    "仅支持 JPEG、PNG、WebP。"
                )
            if width * height > MAX_IMAGE_PIXELS:
                raise AssetDownloadError("图片像素规模过大，已拒绝（安全限制）。")
            image.load()  # 完整解码校验
        # verify 需要重新打开
        with Image.open(path) as image:
            image.verify()
    except AssetDownloadError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AssetDownloadError("图片文件已损坏或不是有效图片。") from exc
    except Image.DecompressionBombError as exc:
        raise AssetDownloadError("图片像素规模过大，已拒绝（安全限制）。") from exc
    return image_format, width, height


class AssetDownloadService:
    """下载候选图片或导入本地图片，产出 SelectedAsset。"""

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_retries = max(0, int(max_retries))
        self._transport = transport
        self._sleep = sleeper

    # ------------------------------------------------------------ 下载

    def download(self, candidate: ImageCandidate, project_root: Path) -> SelectedAsset:
        """下载候选图片到项目 assets/，返回 SelectedAsset。"""
        assets_dir = project_root / ASSETS_DIR_NAME
        assets_dir.mkdir(parents=True, exist_ok=True)
        base_name = f"{candidate.provider}_{_safe_id(candidate.asset_id)}"

        cached = self._find_valid_cached(assets_dir, base_name)
        if cached is not None:
            logger.info("素材缓存命中：%s", cached.name)
            return self._build_asset(candidate, cached, project_root)

        part_path = assets_dir / f"{base_name}.part"
        try:
            self._download_to_part(candidate.download_url, part_path)
            image_format, _, _ = _validate_image_file(part_path)
            final_path = assets_dir / f"{base_name}{_FORMAT_EXTENSIONS[image_format]}"
            part_path.replace(final_path)  # 原子移动
        finally:
            part_path.unlink(missing_ok=True)
        logger.info("素材下载完成：%s", final_path.name)
        return self._build_asset(candidate, final_path, project_root)

    def fetch_preview(self, url: str, max_bytes: int = 2 * 1024 * 1024) -> bytes:
        """获取预览缩略图字节（供 UI 显示；失败抛出异常，由 UI 显示占位）。"""
        if not url:
            raise AssetDownloadError("没有可用的预览图。")
        with httpx.Client(
            timeout=self._timeout, follow_redirects=True, transport=self._transport
        ) as client:
            try:
                response = client.get(url, headers={"User-Agent": USER_AGENT})
            except httpx.HTTPError as exc:
                raise AssetDownloadNetworkError("预览图加载失败。") from exc
        if response.status_code != 200:
            raise AssetDownloadError("预览图加载失败。")
        content = response.content
        if len(content) > max_bytes:
            raise AssetDownloadError("预览图过大。")
        return content

    # ------------------------------------------------------------ 本地导入

    def import_local_file(self, file_path: Path, project_root: Path) -> SelectedAsset:
        """将本地图片复制进项目 assets/（不移动原文件），返回 SelectedAsset。"""
        source = Path(file_path)
        if not source.is_file():
            raise AssetDownloadError(f"找不到文件：{source}")
        image_format, width, height = _validate_image_file(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        assets_dir = project_root / ASSETS_DIR_NAME
        assets_dir.mkdir(parents=True, exist_ok=True)
        final_path = assets_dir / f"local_{digest}{_FORMAT_EXTENSIONS[image_format]}"
        if not final_path.exists():
            temp_path = assets_dir / f"local_{digest}.part"
            try:
                shutil.copyfile(source, temp_path)
                _validate_image_file(temp_path)
                temp_path.replace(final_path)
            finally:
                temp_path.unlink(missing_ok=True)
        return SelectedAsset(
            provider=LOCAL_PROVIDER,
            source=LOCAL_PROVIDER,
            asset_id=digest,
            title=source.name,
            local_path=f"{ASSETS_DIR_NAME}/{final_path.name}",
            source_page="",
            author="",
            author_url="",
            license=USER_PROVIDED_LICENSE,
            license_version="",
            license_url="",
            attribution="",
            width=width,
            height=height,
        )

    # ------------------------------------------------------------ 内部

    def _find_valid_cached(self, assets_dir: Path, base_name: str) -> Path | None:
        for extension in _FORMAT_EXTENSIONS.values():
            candidate_path = assets_dir / f"{base_name}{extension}"
            if candidate_path.is_file():
                try:
                    _validate_image_file(candidate_path)
                except AssetDownloadError:
                    logger.warning("缓存文件校验失败，不复用：%s", candidate_path.name)
                    continue
                return candidate_path
        return None

    def _download_to_part(self, url: str, part_path: Path) -> None:
        if not url:
            raise AssetDownloadError("候选图片没有下载地址。")
        headers = {"User-Agent": USER_AGENT}
        total_attempts = 1 + self._max_retries
        last_error: AssetDownloadError = AssetDownloadNetworkError(
            "图片下载失败，请稍后重试。"
        )
        with httpx.Client(
            timeout=self._timeout, follow_redirects=True, transport=self._transport
        ) as client:
            for attempt in range(1, total_attempts + 1):
                try:
                    self._stream_once(client, url, headers, part_path)
                    return
                except AssetDownloadNetworkError as exc:
                    last_error = exc
                    if attempt < total_attempts:
                        self._sleep(0.5 * attempt)
                # 非网络类 AssetDownloadError（格式/超限）直接向上抛，不重试
        raise last_error

    def _stream_once(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        part_path: Path,
    ) -> None:
        try:
            with client.stream("GET", url, headers=headers) as response:
                status = response.status_code
                if status == 429 or 500 <= status < 600:
                    raise AssetDownloadNetworkError(
                        f"图片服务暂时不可用（{status}），稍后会自动重试。"
                    )
                if status != 200:
                    raise AssetDownloadError(
                        f"图片下载被拒绝（{status}）。请尝试其他候选图片。"
                    )
                received = 0
                with open(part_path, "wb") as handle:
                    for chunk in response.iter_bytes():
                        received += len(chunk)
                        if received > MAX_DOWNLOAD_BYTES:
                            raise AssetDownloadError("图片超过 15MB 大小限制。")
                        handle.write(chunk)
        except httpx.TimeoutException as exc:
            raise AssetDownloadNetworkError("图片下载超时。") from exc
        except httpx.TransportError as exc:
            raise AssetDownloadNetworkError("网络连接失败。") from exc

    def _build_asset(
        self, candidate: ImageCandidate, final_path: Path, project_root: Path
    ) -> SelectedAsset:
        _, width, height = _validate_image_file(final_path)
        return SelectedAsset(
            provider=candidate.provider,
            source=candidate.source,
            asset_id=candidate.asset_id,
            title=candidate.title,
            local_path=f"{ASSETS_DIR_NAME}/{final_path.name}",
            source_page=candidate.source_page,
            author=candidate.author,
            author_url=candidate.author_url,
            license=candidate.license,
            license_version=candidate.license_version,
            license_url=candidate.license_url,
            attribution=candidate.attribution,
            width=width,
            height=height,
        )
