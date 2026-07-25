"""LLM 场景拆分器：LLM 只选择拆分边界，原文由程序重建。

新协议：
- 程序将原文拆成不可修改的编号单元
- LLM 只返回 {"split_after": [0, 2, ...]}
- 最终场景文字由程序从原文单元拼接，避免模型改写原文

旧的 scenes 字符串协议仍保留兼容，但必须通过原文不变量校验。
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

# 这些标点之后可以作为候选拆分点。
_ATOMIC_ENDERS = frozenset("。！？!?；;，,：:\n")

_PROMPT_TEMPLATE = """你是一个视频分镜助手。

原始文案：
{script}

程序已经把原文拆成以下不可修改的最小单元：
{units}

严格规则：
1. 你只能决定在哪些单元之后结束一个场景。
2. 不要重新输出、改写、概括或复制任何原文文字。
3. 场景建议为 {min_length} 到 {max_length} 个字，这是软目标。
4. split_after 中填写“场景结束位置”的单元 id。
5. 不需要填写最后一个单元的 id，程序会自动结束最后一个场景。
6. id 必须来自上面的单元列表。
7. 只输出一个 JSON 对象，不要输出解释或 Markdown。

示例：
共有 5 个单元，希望在单元 1 和单元 3 后拆分：
{{"split_after": [1, 3]}}

输出格式：
{{"split_after": []}}
"""

SCENE_SPLIT_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "scene_split_result",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "split_after": {
                    "type": "array",
                    "items": {
                        "type": "integer",
                        "minimum": 0,
                    },
                }
            },
            "required": ["split_after"],
            "additionalProperties": False,
        },
    },
}


class LLMSplitError(Exception):
    """LLM 拆分结果无效。消息面向用户。"""


def _split_into_units(text: str) -> list[str]:
    """将原文拆成不可修改的候选单元，并保证可无损拼回原文。"""
    if not text:
        return []

    units: list[str] = []
    start = 0
    index = 0

    while index < len(text):
        if text[index] not in _ATOMIC_ENDERS:
            index += 1
            continue

        end = index + 1

        # 连续标点属于同一个单元。
        while end < len(text) and text[end] in _ATOMIC_ENDERS:
            end += 1

        # 标点后的空白保留在前一个单元中。
        while end < len(text) and text[end].isspace():
            end += 1

        units.append(text[start:end])
        start = end
        index = end

    if start < len(text):
        units.append(text[start:])

    units = [unit for unit in units if unit]

    if "".join(units) != text:
        logger.error("原文候选单元无法无损重建")
        raise LLMSplitError("文案拆分准备失败。你可以改用规则拆分。")

    return units


def _format_units(units: list[str]) -> str:
    return "\n".join(
        f"[{index}] {json.dumps(unit, ensure_ascii=False)}"
        for index, unit in enumerate(units)
    )


def build_prompt(
    cleaned_script: str,
    min_length: int = DEFAULT_MIN_SCENE_LENGTH,
    max_length: int = DEFAULT_MAX_SCENE_LENGTH,
) -> str:
    """构造只选择边界、不重新输出原文的提示词。"""
    units = _split_into_units(cleaned_script)
    return _PROMPT_TEMPLATE.format(
        script=cleaned_script,
        units=_format_units(units),
        min_length=min_length,
        max_length=max_length,
    )


def strip_code_fence(text: str) -> str:
    """剥离完整 JSON Markdown 代码块。"""
    stripped = text.strip()
    match = _CODE_BLOCK_PATTERN.match(stripped)
    return match.group("body").strip() if match else stripped


def _log_invalid_shape(data: object, items: object = None) -> None:
    """只记录结构类型，不记录用户文案或模型响应内容。"""
    item_types = "-"

    if isinstance(items, list):
        item_types = (
            ",".join(sorted({type(item).__name__ for item in items}))
            or "-"
        )

    logger.warning(
        "LLM 响应结构不符合要求：顶层类型=%s，数组项类型=%s",
        type(data).__name__,
        item_types,
    )


def parse_response(raw: str) -> list[str]:
    """兼容旧 scenes 协议；最终仍须通过原文不变量校验。"""
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
        scenes = data
    else:
        _log_invalid_shape(data)
        raise LLMSplitError(
            "模型返回格式不符合要求。你可以重试，或改用规则拆分。"
        )

    if not isinstance(scenes, list) or not scenes:
        _log_invalid_shape(
            data,
            scenes if isinstance(scenes, list) else None,
        )
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


def parse_split_after(raw: str, last_index: int) -> list[int]:
    """解析新协议中的场景结束单元编号。"""
    text = strip_code_fence(raw)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMSplitError(
            "模型返回格式不符合要求（不是有效的 JSON）。"
            "你可以重试，或改用规则拆分。"
        ) from exc

    items: object

    if isinstance(data, dict):
        items = None

        # 兼容少数 Provider 使用的近义包装字段。
        for key in ("split_after", "boundaries", "ends"):
            if key in data:
                items = data[key]
                break

        if items is None:
            raise LLMSplitError(
                "模型返回格式不符合要求（缺少 split_after 字段）。"
                "你可以重试，或改用规则拆分。"
            )
    elif isinstance(data, list) and data:
        # 兼容直接返回非空整数数组的 Provider。
        items = data
    else:
        raise LLMSplitError(
            "模型返回格式不符合要求（需要 split_after 数组）。"
            "你可以重试，或改用规则拆分。"
        )

    if not isinstance(items, list):
        _log_invalid_shape(data)
        raise LLMSplitError(
            "模型返回格式不符合要求（split_after 必须是数组）。"
            "你可以重试，或改用规则拆分。"
        )

    indexes: list[int] = []

    for item in items:
        value: object = item

        # 兼容 [{"index": 0}, {"end": 2}]。
        if isinstance(item, dict):
            value = None
            for key in ("index", "end", "after", "unit"):
                if key in item:
                    value = item[key]
                    break

        if isinstance(value, bool):
            value = None

        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())

        if not isinstance(value, int):
            _log_invalid_shape(data, items)
            raise LLMSplitError(
                "模型返回格式不符合要求（拆分位置必须是整数）。"
                "你可以重试，或改用规则拆分。"
            )

        if value < 0 or value > last_index:
            raise LLMSplitError(
                "模型返回了超出文案范围的拆分位置。"
                "你可以重试，或改用规则拆分。"
            )

        indexes.append(value)

    # 拆分位置本质上是集合：排序并去重。
    indexes = sorted(set(indexes))

    # 最后一个单元天然结束，不需要作为显式拆分点。
    return [index for index in indexes if index != last_index]


def _build_scenes_from_split_after(
    units: list[str],
    split_after: list[int],
) -> list[str]:
    """根据边界编号从原文单元重建场景。"""
    if not units:
        return []

    endpoints = [*split_after, len(units) - 1]
    scenes: list[str] = []
    start = 0

    for end in endpoints:
        scene = "".join(units[start : end + 1])

        if scene:
            scenes.append(scene)

        start = end + 1

    return scenes


class LLMSceneSplitter(SceneSplitter):
    """基于 LLM 边界选择的场景拆分器。"""

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

        units = _split_into_units(cleaned_script)

        if len(units) == 1:
            return [cleaned_script]

        prompt = build_prompt(
            cleaned_script,
            self._min_length,
            self._max_length,
        )

        raw = self._client.send(
            prompt,
            response_format=SCENE_SPLIT_RESPONSE_FORMAT,
        )

        try:
            split_after = parse_split_after(raw, len(units) - 1)
        except LLMSplitError as boundary_error:
            # 兼容旧 Provider 返回的 scenes 字符串协议。
            try:
                texts = parse_response(raw)
            except LLMSplitError:
                raise boundary_error
            logger.info("LLM 使用旧 scenes 协议返回结果")
        else:
            texts = _build_scenes_from_split_after(units, split_after)

        if normalize_for_comparison(
            "".join(texts)
        ) != normalize_for_comparison(cleaned_script):
            logger.warning("LLM 拆分结果未通过不变量校验，已拒绝")
            raise LLMSplitError(
                "模型修改了原文文字（增删或改写），结果已被拒绝。"
                "你可以重试，或改用规则拆分。"
            )

        logger.info("LLM 拆分完成：%d 个场景", len(texts))
        return texts
