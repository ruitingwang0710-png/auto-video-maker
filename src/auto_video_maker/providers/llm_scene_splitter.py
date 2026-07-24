"""LLM 场景拆分器：实现 SceneSplitter 接口。

- 只返回 list[str]，不创建 Scene
- LLM 只能拆分原文；拼接不变量校验失败即拒绝结果
- 响应解析保持保守：只接受完整 JSON 数组或 ```json 代码块中的 JSON 数组
"""

from __future__ import annotations

import json
import logging
import re

from auto_video_maker.providers.llm_client import LLMClient
from auto_video_maker.services.scene_splitter import (
    DEFAULT_MAX_SCENE_LENGTH,
    DEFAULT_MIN_SCENE_LENGTH,
    SceneSplitter,
)
from auto_video_maker.services.script_parser import normalize_for_comparison

logger = logging.getLogger(__name__)

_CODE_BLOCK_PATTERN = re.compile(
    r"\A\s*```(?:json)?\s*\n(?P<body>.*?)\n\s*```\s*\Z",
    re.DOTALL,
)

_PROMPT_TEMPLATE = """你是一个视频分镜助手。请把下面的中文文案拆分成多个场景。

严格规则：
1. 只能在原文中选择拆分点，绝对不能改写、删除、添加或调换任何文字。
2. 所有场景按原文顺序拼接后必须与原文完全一致。
3. 每个场景建议 {min_length} 到 {max_length} 个字（软目标，不得为满足长度改动文字）。
4. 只输出一个 JSON 字符串数组，不要输出任何解释或其他内容。

文案：
{script}
"""


class LLMSplitError(Exception):
    """LLM 拆分结果无效。消息面向用户。"""


def build_prompt(
    cleaned_script: str,
    min_length: int = DEFAULT_MIN_SCENE_LENGTH,
    max_length: int = DEFAULT_MAX_SCENE_LENGTH,
) -> str:
    """构造只拆分、不改写的提示词。"""
    return _PROMPT_TEMPLATE.format(
        min_length=min_length, max_length=max_length, script=cleaned_script
    )


def parse_response(raw: str) -> list[str]:
    """保守解析模型响应。

    只接受两种形式：完整 JSON 数组，或 ```json 代码块中的 JSON 数组。
    不从杂质文本中贪婪提取方括号内容。
    """
    text = raw.strip()
    match = _CODE_BLOCK_PATTERN.match(text)
    if match:
        text = match.group("body").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMSplitError("模型返回的内容不是有效的 JSON 数组。") from exc
    if not isinstance(data, list) or not data:
        raise LLMSplitError("模型返回的 JSON 不是非空数组。")
    result: list[str] = []
    for item in data:
        if not isinstance(item, str) or not item.strip():
            raise LLMSplitError("模型返回的数组包含非文本或空白项。")
        result.append(item)
    return result


class LLMSceneSplitter(SceneSplitter):
    """基于 LLM 的场景拆分器。"""

    def __init__(
        self,
        client: LLMClient,
        min_length: int = DEFAULT_MIN_SCENE_LENGTH,
        max_length: int = DEFAULT_MAX_SCENE_LENGTH,
    ) -> None:
        self._client = client
        self._min_length = min_length
        self._max_length = max_length

    def split(self, cleaned_script: str) -> list[str]:
        if not cleaned_script.strip():
            return []
        prompt = build_prompt(cleaned_script, self._min_length, self._max_length)
        raw = self._client.send(prompt)
        texts = parse_response(raw)
        if normalize_for_comparison("".join(texts)) != normalize_for_comparison(
            cleaned_script
        ):
            logger.warning("LLM 拆分结果未通过不变量校验，已拒绝")
            raise LLMSplitError(
                "模型修改了原文文字（增删或改写），结果已被拒绝。"
                "你可以重试，或改用规则拆分。"
            )
        logger.info("LLM 拆分完成：%d 个场景", len(texts))
        return texts
