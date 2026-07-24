"""KeywordService 测试（FakeLLMClient，不发真实请求）。"""

import json
from pathlib import Path

import pytest

from auto_video_maker.infrastructure.config import ConfigStore, LLMSettings
from auto_video_maker.providers.llm_client import LLMClient
from auto_video_maker.services.keyword_service import (
    KeywordService,
    KeywordServiceError,
)
from auto_video_maker.services.smart_split_service import LLMAvailability

SCENE_TEXT = "悉尼歌剧院坐落在海边，白色的帆形屋顶在阳光下闪闪发光。游客们在广场上拍照留念。"


class FakeLLMClient(LLMClient):
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def send(self, prompt: str, response_format: dict | None = None) -> str:
        self.prompts.append(prompt)
        return self.response


def make_service(
    tmp_path: Path,
    response: str = '["sydney opera house", "white sail roof"]',
    available: bool = True,
) -> tuple[KeywordService, FakeLLMClient]:
    client = FakeLLMClient(response)
    config_store = ConfigStore(tmp_path / "config.json")
    config_store.save(LLMSettings(enabled=True, base_url="https://a.com/v1", model="m"))
    service = KeywordService(
        config_store,
        availability_check=lambda: LLMAvailability(available, "" if available else "未配置"),
        llm_client_factory=lambda settings: client,
    )
    return service, client


class TestLLMGeneration:
    def test_success(self, tmp_path: Path) -> None:
        service, client = make_service(tmp_path)
        keywords = service.generate_with_llm(SCENE_TEXT)
        assert keywords == ["sydney opera house", "white sail roof"]
        assert SCENE_TEXT in client.prompts[0]

    def test_unavailable_raises(self, tmp_path: Path) -> None:
        service, client = make_service(tmp_path, available=False)
        with pytest.raises(KeywordServiceError, match="未配置"):
            service.generate_with_llm(SCENE_TEXT)
        assert client.prompts == []  # 不可用时零调用

    def test_code_block_response_parsed(self, tmp_path: Path) -> None:
        body = json.dumps(["harbor city sunset"])
        service, _ = make_service(tmp_path, response=f"```json\n{body}\n```")
        assert service.generate_with_llm(SCENE_TEXT) == ["harbor city sunset"]

    def test_invalid_response_raises(self, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path, response="这不是 JSON")
        with pytest.raises(KeywordServiceError, match="格式无效"):
            service.generate_with_llm(SCENE_TEXT)

    def test_keywords_capped_at_three_and_deduped(self, tmp_path: Path) -> None:
        response = json.dumps(["a b", "a b", "c d", "e f", "g h"])
        service, _ = make_service(tmp_path, response=response)
        assert service.generate_with_llm(SCENE_TEXT) == ["a b", "c d", "e f"]

    def test_long_keyword_clamped_to_200(self, tmp_path: Path) -> None:
        response = json.dumps(["x" * 500])
        service, _ = make_service(tmp_path, response=response)
        keywords = service.generate_with_llm(SCENE_TEXT)
        assert len(keywords[0]) == 200

    def test_empty_scene_text_raises(self, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path)
        with pytest.raises(KeywordServiceError, match="场景文字为空"):
            service.generate_with_llm("   ")

    def test_blank_items_filtered_not_rejected(self, tmp_path: Path) -> None:
        """关键词契约：数组含空白项不整体拒绝，由 normalize 过滤。

        （macOS test_ai_keywords_normalized 失败的根因回归锚点：
        Fake 与真实 LLMClient 一致，返回原始 JSON 文本。）
        """
        response = json.dumps(["video editing", "", "VIDEO EDITING", "editing software"])
        service, _ = make_service(tmp_path, response=response)
        assert service.generate_with_llm(SCENE_TEXT) == [
            "video editing", "editing software"
        ]

    def test_null_and_object_items_filtered(self, tmp_path: Path) -> None:
        response = json.dumps(["good keyword", None, {"kw": "x"}, 42])
        service, _ = make_service(tmp_path, response=response)
        assert service.generate_with_llm(SCENE_TEXT) == ["good keyword"]

    def test_all_items_invalid_raises(self, tmp_path: Path) -> None:
        response = json.dumps(["", "   ", None])
        service, _ = make_service(tmp_path, response=response)
        with pytest.raises(KeywordServiceError, match="没有返回可用的关键词"):
            service.generate_with_llm(SCENE_TEXT)

    def test_object_response_rejected_for_keywords(self, tmp_path: Path) -> None:
        """关键词只接受顶层数组；对象协议属于分镜契约。"""
        response = json.dumps({"scenes": ["video editing"]})
        service, _ = make_service(tmp_path, response=response)
        with pytest.raises(KeywordServiceError, match="格式无效"):
            service.generate_with_llm(SCENE_TEXT)


class TestNormalizeKeywords:
    """normalize_keywords：进入 UI/持久化前的唯一规范化入口。"""

    def test_spec_example(self) -> None:
        """需求给定用例：strip、去空、大小写不敏感去重、保序。"""
        assert KeywordService.normalize_keywords(
            ["video editing", "", "VIDEO EDITING", "editing software"]
        ) == ["video editing", "editing software"]

    def test_strips_and_collapses_whitespace(self) -> None:
        assert KeywordService.normalize_keywords(["  a   b  ", "\tc\n"]) == ["a b", "c"]

    def test_case_insensitive_dedupe_keeps_first(self) -> None:
        assert KeywordService.normalize_keywords(["Opera House", "opera house", "OPERA HOUSE"]) == [
            "Opera House"
        ]

    def test_caps_at_three(self) -> None:
        assert KeywordService.normalize_keywords(["a", "b", "c", "d", "e"]) == ["a", "b", "c"]

    def test_clamps_length_to_200(self) -> None:
        result = KeywordService.normalize_keywords(["x" * 500])
        assert len(result[0]) == 200

    def test_non_string_and_blank_items_removed(self) -> None:
        assert KeywordService.normalize_keywords(["ok", None, 123, "   "]) == ["ok"]

    def test_empty_input(self) -> None:
        assert KeywordService.normalize_keywords([]) == []
        assert KeywordService.normalize_keywords(None) == []


class TestLLMCaseInsensitiveDedupe:
    def test_generate_with_llm_dedupes_case_insensitively(self, tmp_path: Path) -> None:
        response = json.dumps(["Video Editing", "video editing", "other words"])
        service, _ = make_service(tmp_path, response=response)
        assert service.generate_with_llm(SCENE_TEXT) == ["Video Editing", "other words"]


class TestFallback:
    def test_fallback_returns_short_text_not_full_original(self, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path)
        result = service.generate_fallback(SCENE_TEXT)
        assert len(result) == 1
        # 只取第一句且去掉句末标点，不是完整原文
        assert result[0] == "悉尼歌剧院坐落在海边，白色的帆形屋顶在阳光下闪闪发光"
        assert result[0] != SCENE_TEXT

    def test_fallback_long_single_sentence_truncated(self, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path)
        result = service.generate_fallback("字" * 300)
        assert len(result[0]) <= 60

    def test_fallback_cleans_whitespace(self, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path)
        result = service.generate_fallback("  海边\n\n 的  歌剧院  ")
        assert result == ["海边 的 歌剧院"]

    def test_fallback_empty_text(self, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path)
        assert service.generate_fallback("   ") == []

    def test_fallback_needs_no_llm_config(self, tmp_path: Path) -> None:
        """Phase 3 不依赖 LLM：不可用时兜底仍然工作。"""
        service, _ = make_service(tmp_path, available=False)
        assert service.generate_fallback(SCENE_TEXT)
