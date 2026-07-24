"""LLMSceneSplitter 测试（FakeLLMClient，不发真实请求）。"""

import json

import pytest

from auto_video_maker.providers.llm_client import LLMClient
from auto_video_maker.providers.llm_scene_splitter import (
    SCENE_SPLIT_RESPONSE_FORMAT,
    LLMSceneSplitter,
    LLMSplitError,
    parse_response,
)
from auto_video_maker.services.scene_splitter import SceneSplitter

SCRIPT = "人工智能正在改变世界。自动化工具无处不在。未来已经到来。"
VALID_SPLIT = ["人工智能正在改变世界。", "自动化工具无处不在。", "未来已经到来。"]


class FakeLLMClient(LLMClient):
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []
        self.response_formats: list[dict | None] = []

    def send(self, prompt: str, response_format: dict | None = None) -> str:
        self.prompts.append(prompt)
        self.response_formats.append(response_format)
        return self.response


def make_splitter(response: str) -> tuple[LLMSceneSplitter, FakeLLMClient]:
    client = FakeLLMClient(response)
    return LLMSceneSplitter(client), client


# 新协议：{"scenes": [...]} → 成功
def test_scenes_object_success() -> None:
    splitter, client = make_splitter(
        json.dumps({"scenes": VALID_SPLIT}, ensure_ascii=False)
    )
    assert splitter.split(SCRIPT) == VALID_SPLIT
    # 提示词包含原文、长度软目标与 scenes 协议说明
    assert SCRIPT in client.prompts[0]
    assert "15" in client.prompts[0] and "60" in client.prompts[0]
    assert '"scenes"' in client.prompts[0]


def test_strict_response_format_passed_to_client() -> None:
    splitter, client = make_splitter(
        json.dumps({"scenes": VALID_SPLIT}, ensure_ascii=False)
    )
    splitter.split(SCRIPT)
    assert client.response_formats == [SCENE_SPLIT_RESPONSE_FORMAT]
    schema = SCENE_SPLIT_RESPONSE_FORMAT["json_schema"]
    assert SCENE_SPLIT_RESPONSE_FORMAT["type"] == "json_schema"
    assert schema["name"] == "scene_split_result"
    assert schema["strict"] is True
    assert schema["schema"]["required"] == ["scenes"]
    assert schema["schema"]["additionalProperties"] is False
    assert schema["schema"]["properties"]["scenes"]["items"] == {"type": "string"}


def test_scenes_object_in_code_block() -> None:
    body = json.dumps({"scenes": VALID_SPLIT}, ensure_ascii=False)
    splitter, _ = make_splitter(f"```json\n{body}\n```")
    assert splitter.split(SCRIPT) == VALID_SPLIT


# scenes 结构非法：object / null / 空白串 / 缺失 / 非 list（逐项拒绝，不强转不跳过）
@pytest.mark.parametrize(
    "payload",
    [
        {"scenes": [{"scene": "人工智能正在改变世界。"}, "自动化工具无处不在。未来已经到来。"]},
        {"scenes": ["人工智能正在改变世界。", None, "未来已经到来。"]},
        {"scenes": ["人工智能正在改变世界。", "   ", "未来已经到来。"]},
        {"scenes": ["人工智能正在改变世界。", 123]},
        {"result": VALID_SPLIT},          # 缺 scenes
        {"scenes": "不是数组"},            # scenes 非 list
        {"scenes": []},                    # 空数组
    ],
)
def test_invalid_scenes_structures_rejected(payload: dict) -> None:
    splitter, _ = make_splitter(json.dumps(payload, ensure_ascii=False))
    with pytest.raises(LLMSplitError, match="格式不符合要求"):
        splitter.split(SCRIPT)


def test_shape_logging_contains_types_not_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """结构诊断日志只记录类型，不泄漏文案或模型响应内容。"""
    secret_text = "机密场景文字内容不可入日志"
    payload = {"scenes": [{"scene": secret_text}]}
    splitter, _ = make_splitter(json.dumps(payload, ensure_ascii=False))
    with caplog.at_level("WARNING"):
        with pytest.raises(LLMSplitError):
            splitter.split(SCRIPT + secret_text)
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "dict" in joined  # 类型信息在
    assert secret_text not in joined  # 内容不在
    assert SCRIPT[:8] not in joined


# 旧协议兼容：完整 JSON 数组 → 成功
def test_plain_json_array_success() -> None:
    splitter, client = make_splitter(json.dumps(VALID_SPLIT, ensure_ascii=False))
    assert splitter.split(SCRIPT) == VALID_SPLIT


# 测试要求 2：```json 代码块 → 成功
def test_json_code_block_success() -> None:
    body = json.dumps(VALID_SPLIT, ensure_ascii=False)
    for wrapped in (f"```json\n{body}\n```", f"```\n{body}\n```"):
        splitter, _ = make_splitter(wrapped)
        assert splitter.split(SCRIPT) == VALID_SPLIT


# 测试要求 3：杂质文本不贪婪提取
def test_junk_text_with_embedded_json_rejected() -> None:
    body = json.dumps(VALID_SPLIT, ensure_ascii=False)
    junk = f"好的，以下是拆分结果：\n{body}\n希望对你有帮助！"
    splitter, _ = make_splitter(junk)
    with pytest.raises(LLMSplitError, match="JSON"):
        splitter.split(SCRIPT)


# 测试要求 4：改写 / 删除 / 添加 → 不变量拒绝
def test_rewritten_text_rejected() -> None:
    altered = ["人工智能正在改造世界。", "自动化工具无处不在。", "未来已经到来。"]
    splitter, _ = make_splitter(json.dumps(altered, ensure_ascii=False))
    with pytest.raises(LLMSplitError, match="修改了原文"):
        splitter.split(SCRIPT)


def test_deleted_text_rejected() -> None:
    missing = ["人工智能正在改变世界。", "未来已经到来。"]
    splitter, _ = make_splitter(json.dumps(missing, ensure_ascii=False))
    with pytest.raises(LLMSplitError, match="修改了原文"):
        splitter.split(SCRIPT)


def test_added_text_rejected() -> None:
    extra = VALID_SPLIT + ["这是模型自己加的结尾。"]
    splitter, _ = make_splitter(json.dumps(extra, ensure_ascii=False))
    with pytest.raises(LLMSplitError, match="修改了原文"):
        splitter.split(SCRIPT)


# 测试要求 5：非 JSON / 空响应 / 空数组
@pytest.mark.parametrize("bad", ["这不是 JSON", "", "   ", "[]", "{}", '["", "x"]',
                                 '[123]'])
def test_invalid_responses_rejected(bad: str) -> None:
    splitter, _ = make_splitter(bad)
    with pytest.raises(LLMSplitError):
        splitter.split(SCRIPT)


def test_whitespace_only_script_returns_empty_without_calling_llm() -> None:
    splitter, client = make_splitter("[]")
    assert splitter.split("   ") == []
    assert client.prompts == []


# 测试要求 11：实现 SceneSplitter 接口，返回 list[str]
def test_implements_scene_splitter_interface() -> None:
    splitter, _ = make_splitter(json.dumps(VALID_SPLIT, ensure_ascii=False))
    assert isinstance(splitter, SceneSplitter)
    result = splitter.split(SCRIPT)
    assert isinstance(result, list)
    assert all(isinstance(item, str) for item in result)


class TestParseResponse:
    def test_rejects_non_list_json(self) -> None:
        with pytest.raises(LLMSplitError):
            parse_response('{"scenes": []}')

    def test_accepts_array_with_whitespace(self) -> None:
        assert parse_response('  ["场景一。","场景二。"]  ') == ["场景一。", "场景二。"]

    # 协议边界直接测试（macOS 失败诊断的确认用例）

    def test_legacy_top_level_array_string_passes(self) -> None:
        assert parse_response('["video editing", "editing software"]') == [
            "video editing", "editing software"
        ]

    def test_new_object_protocol_string_passes(self) -> None:
        assert parse_response('{"scenes":["video editing","editing software"]}') == [
            "video editing", "editing software"
        ]

    @pytest.mark.parametrize("payload", [
        '[{"scene": "x"}, "ok"]',   # 含 object
        '["ok", null]',              # 含 null
        '["ok", "   "]',             # 含空白项（分镜契约必须整体拒绝）
    ])
    def test_top_level_array_with_invalid_items_rejected(self, payload: str) -> None:
        with pytest.raises(LLMSplitError, match="格式不符合要求"):
            parse_response(payload)
