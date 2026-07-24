"""图片搜索：ImageProvider 接口与 Openverse 实现。

- 只从开放许可来源获取；默认许可过滤 cc0、pdm、by（排除 NC/ND/SA）
- 匿名访问 Openverse 官方 API，处理限流（429 按 Retry-After 或退避）
- query 统一截断至 200 字符
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

import httpx

from auto_video_maker import __version__

logger = logging.getLogger(__name__)

USER_AGENT = f"AutoVideoMaker/{__version__} (https://github.com/auto-video-maker)"
OPENVERSE_API_URL = "https://api.openverse.org/v1/images/"
ALLOWED_LICENSES = "cc0,pdm,by"
MAX_QUERY_LENGTH = 200
DEFAULT_PER_PAGE = 12


class ImageProviderError(Exception):
    """图片搜索失败。消息面向用户，可直接展示。"""


class ImageProviderNetworkError(ImageProviderError):
    """网络错误 / 超时 / 限流耗尽。可重试。"""


@dataclass
class ImageCandidate:
    """统一候选图片结构（字段映射见 TASK.md）。"""

    provider: str
    source: str
    asset_id: str
    title: str
    preview_url: str
    download_url: str
    source_page: str
    author: str
    author_url: str
    license: str
    license_version: str
    license_url: str
    attribution: str
    width: int | None = None
    height: int | None = None


def clamp_query(query: str) -> str:
    """清理空白并截断到 200 字符。"""
    cleaned = " ".join(query.split())
    return cleaned[:MAX_QUERY_LENGTH]


class ImageProvider(ABC):
    """图片搜索统一接口。"""

    @abstractmethod
    def search(self, query: str, per_page: int = DEFAULT_PER_PAGE) -> list[ImageCandidate]:
        """搜索候选图片；无结果返回空列表，网络失败抛出异常。"""


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class OpenverseImageProvider(ImageProvider):
    """Openverse 官方 API（匿名访问）。"""

    def __init__(
        self,
        api_url: str = OPENVERSE_API_URL,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_url = api_url
        self._timeout = timeout_seconds
        self._max_retries = max(0, int(max_retries))
        self._transport = transport
        self._sleep = sleeper

    def search(self, query: str, per_page: int = DEFAULT_PER_PAGE) -> list[ImageCandidate]:
        cleaned = clamp_query(query)
        if not cleaned:
            return []
        params = {
            "q": cleaned,
            "license": ALLOWED_LICENSES,
            "page_size": per_page,
        }
        headers = {"User-Agent": USER_AGENT}
        total_attempts = 1 + self._max_retries
        last_error = ImageProviderNetworkError("图片搜索失败，请稍后重试。")

        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            for attempt in range(1, total_attempts + 1):
                try:
                    response = client.get(self._api_url, params=params, headers=headers)
                except httpx.TimeoutException:
                    last_error = ImageProviderNetworkError(
                        "图片搜索超时。请检查网络后重试。"
                    )
                except httpx.TransportError:
                    last_error = ImageProviderNetworkError(
                        "无法连接图片服务。请检查网络后重试。"
                    )
                else:
                    status = response.status_code
                    if status == 200:
                        return self._parse_results(response)
                    if status == 429:
                        last_error = ImageProviderNetworkError(
                            "图片服务请求过于频繁（429），稍后会自动重试。"
                        )
                        self._wait_before_retry(attempt, total_attempts, response)
                        continue
                    if 500 <= status < 600:
                        last_error = ImageProviderNetworkError(
                            f"图片服务暂时异常（{status}），稍后会自动重试。"
                        )
                        self._wait_before_retry(attempt, total_attempts, None)
                        continue
                    # 其他 4xx / 3xx：不重试
                    raise ImageProviderError(
                        f"图片服务返回了意外状态（{status}）。请检查搜索关键词。"
                    )
                if attempt < total_attempts:
                    self._wait_before_retry(attempt, total_attempts, None)
        raise last_error

    # ------------------------------------------------------------ 内部

    def _wait_before_retry(
        self, attempt: int, total_attempts: int, response: httpx.Response | None
    ) -> None:
        if attempt >= total_attempts:
            return
        delay = 0.5 * attempt
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    delay = max(0.0, min(float(retry_after), 30.0))
                except ValueError:
                    pass
        self._sleep(delay)

    def _parse_results(self, response: httpx.Response) -> list[ImageCandidate]:
        try:
            data = response.json()
            results = data.get("results", [])
        except ValueError as exc:
            raise ImageProviderError("无法解析图片服务的响应。") from exc
        candidates: list[ImageCandidate] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("id") or "")
            download_url = str(item.get("url") or "")
            if not asset_id or not download_url:
                continue
            candidates.append(
                ImageCandidate(
                    provider="openverse",
                    source=str(item.get("source") or ""),
                    asset_id=asset_id,
                    title=str(item.get("title") or ""),
                    preview_url=str(item.get("thumbnail") or ""),
                    download_url=download_url,
                    source_page=str(item.get("foreign_landing_url") or ""),
                    author=str(item.get("creator") or ""),
                    author_url=str(item.get("creator_url") or ""),
                    license=str(item.get("license") or ""),
                    license_version=str(item.get("license_version") or ""),
                    license_url=str(item.get("license_url") or ""),
                    attribution=str(item.get("attribution") or ""),
                    width=_int_or_none(item.get("width")),
                    height=_int_or_none(item.get("height")),
                )
            )
        logger.info("图片搜索完成：%d 个候选", len(candidates))
        return candidates
