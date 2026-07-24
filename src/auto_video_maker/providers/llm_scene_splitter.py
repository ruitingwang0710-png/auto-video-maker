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
4. 只输出一个 JSON 对象，格式为 {{"scenes": ["场景一原文", "场景二原文"]}}。
   scenes 数组的每一项必须是字符串，不要输出任何解释或其他内容。

文案：
{script}
"""

# OpenAI 兼容 strict JSON Schema：统一返回 {"scenes": [...]}
SCENE_SPLIT_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "scene_split_result",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "scenes": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "required": ["scenes"],
            "additionalProperties": False,
        },
    },
}


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


def strip_code_fence(text: str) -> str:
    """剥离 ```json 代码块包裹（无包裹则原样返回，均去首尾空白）。"""
    stripped = text.strip()
    match = _CODE_BLOCK_PATTERN.match(stripped)
    return match.group("body").strip() if match else stripped


def _log_invalid_shape(data: object, items: object = None) -> None:
    """记录结构诊断信息：只记类型，不记用户文案或模型响应内容。"""
    item_types: str = "-"
    if isinstance(items, list):
        item_types = ",".join(sorted({type(item).__name__ for item in items})) or "-"
    logger.warning(
        "LLM 响应结构不符合要求：顶层类型=%s，scenes 项类型=%s",
        type(data).__name__,
        item_types,
    )


def parse_response(raw: str) -> list[str]:
    """保守解析模型响应，返回场景文字列表。

    接受的形式（含 ```json 代码块包裹）：
    - 新协议：{"scenes": ["...", "..."]}
    - 旧协议兼容：顶层 JSON 字符串数组
    不从杂质文本中贪婪提取内容。

    客户端验证（即使 strict schema 已生效仍执行）：
    scenes 必须是 list，每项必须是 str 且去除空白后非空，至少一个场景。
    不强制转换非法项、不跳过非法项。
    """
    text = strip_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMSplitError(
            "模型返回格式不符合要求（不是有效的 JSON）。"
            "你可以重试，或改用规则拆分。"
        ) from exc
    if isinstance(data, dict):
        if "scenes" not in data:
            _log_invalid_shape(data)
            raise LLMSplitError(
                "模型返回格式不符合要求（缺少 scenes 字段）。"
                "你可以重试，或改用规则拆分。"
            )
        scenes = data["scenes"]
    elif isinstance(data, list):
        scenes = data  # 旧协议兼容
    else:
        _log_invalid_shape(data)
        raise LLMSplitError(
            "模型返回格式不符合要求。你可以重试，或改用规则拆分。"
        )
    if not isinstance(scenes, list) or not scenes:
        _log_invalid_shape(data, scenes if isinstance(scenes, list) else None)
        raise LLMSplitError(
            "模型返回格式不符合要求（scenes 必须是非空数组）。"
            "你可以重试，或改用规则拆分。"
        )
    for item in scenes:
        if not isinstance(item, str) or not item.strip():
            _log_invalid_shape(data, scenes)
            raise LLMSplitError(
                "模型返回格式不符合要求（scenes 每项必须是非空文本）。"
                "你可以重试，或改用规则拆分。"
            )
    return list(scenes)


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
        raw = self._client.send(prompt, response_format=SCENE_SPLIT_RESPONSE_FORMAT)
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
