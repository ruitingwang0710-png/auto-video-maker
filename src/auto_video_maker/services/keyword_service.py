"""关键词服务：LLM 可选生成 + 规则兜底。

- Phase 3 不依赖真实 LLM 配置：未配置时兜底与手动编辑完全可用
- 兜底不无条件提交完整场景原文：清理空白并生成简短搜索文本
- 最终 query 统一 ≤ 200 字符（clamp_query）
- 不修改 Scene；写入经 SceneService.set_scene_keywords
"""

from __future__ import annotations

import logging
from typing import Callable

import json

from auto_video_maker.infrastructure.config import ConfigStore, LLMSettings
from auto_video_maker.providers.image_provider import clamp_query
from auto_video_maker.providers.llm_client import LLMClient
from auto_video_maker.providers.llm_scene_splitter import strip_code_fence
from auto_video_maker.services.script_parser import SENTENCE_ENDERS, split_after_punctuation
from auto_video_maker.services.smart_split_service import LLMAvailability

logger = logging.getLogger(__name__)

FALLBACK_MAX_CHARS = 60
MAX_KEYWORDS = 3

_PROMPT_TEMPLATE = """你是一个图库搜索助手。请为下面的视频场景文案生成用于搜索
开放许可图片的英文关键词。

严格规则：
1. 生成 1 到 {max_keywords} 个英文搜索短语，每个不超过 6 个单词。
2. 短语应描述画面内容（物体、场景、动作），不要抽象概念。
3. 只输出一个 JSON 字符串数组，不要输出任何解释或其他内容。

场景文案：
{scene_text}
"""


class KeywordServiceError(Exception):
    """关键词生成失败。消息面向用户。"""


def build_keyword_prompt(scene_text: str) -> str:
    return _PROMPT_TEMPLATE.format(max_keywords=MAX_KEYWORDS, scene_text=scene_text)


class KeywordService:
    """为场景生成搜索关键词。"""

    @staticmethod
    def normalize_keywords(items: list[str] | None) -> list[str]:
        """关键词列表进入 UI/持久化前的唯一规范化入口。

        - strip 每项并压缩内部空白（复用 clamp_query，含 200 字符限长）
        - 移除空白项与非文本项
        - 大小写不敏感去重，保留原始顺序
        - 最多保留 MAX_KEYWORDS 项
        """
        result: list[str] = []
        seen: set[str] = set()
        for item in items or []:
            if not isinstance(item, str):
                continue
            keyword = clamp_query(item)
            if not keyword:
                continue
            key = keyword.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(keyword)
            if len(result) >= MAX_KEYWORDS:
                break
        return result

    def __init__(
        self,
        config_store: ConfigStore,
        availability_check: Callable[[], LLMAvailability],
        llm_client_factory: Callable[[LLMSettings], LLMClient],
    ) -> None:
        self._config_store = config_store
        self._availability_check = availability_check
        self._llm_client_factory = llm_client_factory

    def llm_availability(self) -> LLMAvailability:
        """LLM 关键词生成是否可用（与智能分镜共用条件）。"""
        return self._availability_check()

    def generate_with_llm(self, scene_text: str) -> list[str]:
        """经 LLM 生成 1–3 个英文搜索关键词。不可用或失败抛出异常。"""
        cleaned = " ".join(scene_text.split())
        if not cleaned:
            raise KeywordServiceError("场景文字为空，无法生成关键词。")
        check = self._availability_check()
        if not check.available:
            raise KeywordServiceError(check.reason)
        settings = self._config_store.load()
        client = self._llm_client_factory(settings)
        raw = client.send(build_keyword_prompt(cleaned))
        items = self._parse_keyword_items(raw)
        keywords = self.normalize_keywords(items)
        if not keywords:
            raise KeywordServiceError(
                "模型没有返回可用的关键词。你可以重试，或手动填写关键词。"
            )
        logger.info("LLM 关键词生成完成：%d 个", len(keywords))
        return keywords

    @staticmethod
    def _parse_keyword_items(raw: str) -> list[str]:
        """解析不同 OpenAI-compatible Provider 返回的关键词结构。"""
        text = strip_code_fence(raw).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "关键词响应不是有效 JSON：%s；原始响应=%r",
                exc,
                raw,
            )
            raise KeywordServiceError(
                "模型返回的关键词格式无效。你可以重试，或手动填写关键词。"
            ) from exc

        # 兼容 {"keywords": [...]}
        # 以及部分 Provider 使用的其他包装字段。
        if isinstance(data, dict):
            for key in ("keywords", "items", "search_terms", "queries"):
                value = data.get(key)
                if isinstance(value, list):
                    data = value
                    break
            else:
                logger.warning(
                    "关键词对象没有可识别的数组字段：keys=%s",
                    list(data.keys()),
                )
                raise KeywordServiceError(
                    "模型返回的关键词格式无效。你可以重试，或手动填写关键词。"
                )

        if not isinstance(data, list):
            logger.warning(
                "关键词顶层结构不是数组：type=%s；data=%r",
                type(data).__name__,
                data,
            )
            raise KeywordServiceError(
                "模型返回的关键词格式无效。你可以重试，或手动填写关键词。"
            )

        items: list[str] = []

        for item in data:
            # 标准格式：["keyword one", "keyword two"]
            if isinstance(item, str):
                items.append(item)
                continue

            # 兼容 [{"keyword": "keyword one"}]
            if isinstance(item, dict):
                for key in ("keyword", "query", "text", "term"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        items.append(value)
                        break

        if not items:
            logger.warning("关键词数组中没有可用文本项：data=%r", data)
            raise KeywordServiceError(
                "模型返回的关键词格式无效。你可以重试，或手动填写关键词。"
            )

        return items

    def generate_fallback(self, scene_text: str) -> list[str]:
        """规则兜底：清理空白并生成简短搜索文本（非完整原文）。"""
        cleaned = " ".join(scene_text.split())
        if not cleaned:
            return []
        sentences = split_after_punctuation(cleaned, SENTENCE_ENDERS)
        short = sentences[0] if sentences else cleaned
        short = short.strip().rstrip(SENTENCE_ENDERS + "。！？!?…")
        if len(short) > FALLBACK_MAX_CHARS:
            short = short[:FALLBACK_MAX_CHARS]
        return [clamp_query(short)] if short.strip() else []
