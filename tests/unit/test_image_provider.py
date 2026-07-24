"""OpenverseImageProvider 测试（httpx.MockTransport，不发真实请求）。"""

import httpx
import pytest

from auto_video_maker.providers.image_provider import (
    ImageCandidate,
    ImageProvider,
    ImageProviderError,
    ImageProviderNetworkError,
    OpenverseImageProvider,
    clamp_query,
)

SAMPLE_RESULT = {
    "id": "abc-123",
    "title": "Sydney Opera House",
    "source": "wikimedia",
    "thumbnail": "https://img.example.com/thumb.jpg",
    "url": "https://img.example.com/full.jpg",
    "foreign_landing_url": "https://commons.example.com/photo/abc-123",
    "creator": "Example Author",
    "creator_url": "https://example.com/author",
    "license": "by",
    "license_version": "4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "attribution": "Photo by Example Author, CC BY 4.0",
    "width": 1920,
    "height": 1080,
}


def make_provider(handler, max_retries: int = 2):
    sleeps: list[float] = []
    provider = OpenverseImageProvider(
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
        sleeper=sleeps.append,
    )
    return provider, sleeps


# 测试要求 1：字段映射
def test_field_mapping() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": [SAMPLE_RESULT]})

    provider, _ = make_provider(handler)
    results = provider.search("Sydney Opera House")
    assert len(results) == 1
    candidate = results[0]
    assert isinstance(candidate, ImageCandidate)
    assert candidate.provider == "openverse"
    assert candidate.source == "wikimedia"
    assert candidate.asset_id == "abc-123"
    assert candidate.preview_url == SAMPLE_RESULT["thumbnail"]
    assert candidate.download_url == SAMPLE_RESULT["url"]
    assert candidate.source_page == SAMPLE_RESULT["foreign_landing_url"]
    assert candidate.author == "Example Author"
    assert candidate.author_url == SAMPLE_RESULT["creator_url"]
    assert candidate.license == "by"
    assert candidate.license_version == "4.0"
    assert candidate.license_url == SAMPLE_RESULT["license_url"]
    assert candidate.attribution == SAMPLE_RESULT["attribution"]
    assert candidate.width == 1920 and candidate.height == 1080


# 测试要求 2：许可过滤参数
def test_license_filter_params() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        captured["ua"] = request.headers.get("User-Agent")
        return httpx.Response(200, json={"results": []})

    provider, _ = make_provider(handler)
    provider.search("opera", per_page=8)
    assert captured["params"]["license"] == "cc0,pdm,by"
    assert captured["params"]["page_size"] == "8"
    assert "AutoVideoMaker" in captured["ua"]


# 测试要求 3：空结果 / 错误分类 / 重试
def test_empty_results_returns_empty_list() -> None:
    provider, _ = make_provider(lambda r: httpx.Response(200, json={"results": []}))
    assert provider.search("nothing") == []


def test_429_honors_retry_after_then_succeeds() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 2:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"results": [SAMPLE_RESULT]})

    provider, sleeps = make_provider(handler)
    assert len(provider.search("opera")) == 1
    assert sleeps == [3.0]


def test_5xx_retries_limited() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    provider, _ = make_provider(handler, max_retries=2)
    with pytest.raises(ImageProviderNetworkError):
        provider.search("opera")
    assert len(calls) == 3  # 1 + max_retries


def test_4xx_no_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400)

    provider, _ = make_provider(handler, max_retries=3)
    with pytest.raises(ImageProviderError):
        provider.search("opera")
    assert len(calls) == 1


def test_network_error_retries() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("offline")

    provider, _ = make_provider(handler, max_retries=1)
    with pytest.raises(ImageProviderNetworkError):
        provider.search("opera")
    assert len(calls) == 2


# 测试要求 4：query 截断
def test_query_clamped_to_200_chars() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["q"] = request.url.params["q"]
        return httpx.Response(200, json={"results": []})

    provider, _ = make_provider(handler)
    provider.search("字" * 500)
    assert len(captured["q"]) == 200


def test_clamp_query_cleans_whitespace() -> None:
    assert clamp_query("  hello \n world  ") == "hello world"
    assert len(clamp_query("x" * 300)) == 200


def test_blank_query_returns_empty_without_request() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"results": []})

    provider, _ = make_provider(handler)
    assert provider.search("   ") == []
    assert calls == []


def test_malformed_entries_skipped() -> None:
    results = [SAMPLE_RESULT, {"id": "", "url": "x"}, "junk", {"id": "no-url"}]

    provider, _ = make_provider(
        lambda r: httpx.Response(200, json={"results": results})
    )
    assert len(provider.search("opera")) == 1


def test_is_image_provider_subclass() -> None:
    provider, _ = make_provider(lambda r: httpx.Response(200, json={"results": []}))
    assert isinstance(provider, ImageProvider)
