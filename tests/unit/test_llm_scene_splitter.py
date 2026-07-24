"""LLMSceneSplitter 测试（FakeLLMClient，不发真实请求）。"""

import json

import pytest

from auto_video_maker.providers.llm_client import LLMClient
from auto_video_maker.providers.llm_scene_splitter import (
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

    def send(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def make_splitter(response: str) -> tuple[LLMSceneSplitter, FakeLLMClient]:
    client = FakeLLMClient(response)
    return LLMSceneSplitter(client), client


# 测试要求 1：完整 JSON 数组 → 成功
def test_plain_json_array_success() -> None:
    splitter, client = make_splitter(json.dumps(VALID_SPLIT, ensure_ascii=False))
    assert splitter.split(SCRIPT) == VALID_SPLIT
    # 提示词包含原文与长度软目标
    assert SCRIPT in client.prompts[0]
    assert "15" in client.prompts[0] and "60" in client.prompts[0]


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
