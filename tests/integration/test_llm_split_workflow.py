"""集成测试：LLM 智能分镜 → 预览 list[str] → 唯一 SceneService 应用 → 保存。

使用 FakeLLMClient 与 httpx.MockTransport，不发真实网络请求。
"""

import json
from pathlib import Path

import httpx
import pytest

from auto_video_maker.infrastructure.config import ConfigStore, LLMSettings
from auto_video_maker.infrastructure.secret_store import (
    FakeSecretStore,
    secret_id_for_base_url,
)
from auto_video_maker.providers.llm_client import OpenAICompatibleClient
from auto_video_maker.providers.llm_scene_splitter import LLMSceneSplitter
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService, ScenesExistError
from auto_video_maker.services.scene_splitter import RuleBasedSceneSplitter
from auto_video_maker.services.smart_split_service import SmartSplitService
from auto_video_maker.services.script_parser import clean_script, normalize_for_comparison

BASE_URL = "https://api.example.com/v1"
SCRIPT = "人工智能正在改变企业的工作方式。自动化工具可以协助企业处理重复任务。未来已经到来。"
LLM_SPLIT = [
    "人工智能正在改变企业的工作方式。",
    "自动化工具可以协助企业处理重复任务。",
    "未来已经到来。",
]


def build_stack(tmp_path: Path):
    """搭建与 app.py composition root 相同结构的组件栈（Mock 传输层）。"""
    config_store = ConfigStore(tmp_path / "config.json")
    secret_store = FakeSecretStore()
    config_store.save(LLMSettings(enabled=True, base_url=BASE_URL, model="m"))
    secret_store.set(secret_id_for_base_url(BASE_URL), "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(LLM_SPLIT, ensure_ascii=False)}}]},
        )

    def factory(settings: LLMSettings):
        client = OpenAICompatibleClient(
            base_url=settings.base_url,
            model=settings.model,
            secret_store=secret_store,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            transport=httpx.MockTransport(handler),
            sleeper=lambda s: None,
        )
        return LLMSceneSplitter(client)

    manager = ProjectManager()
    rule_splitter = RuleBasedSceneSplitter()
    scene_service = SceneService(rule_splitter, manager)
    smart = SmartSplitService(config_store, secret_store, rule_splitter, factory)
    return manager, scene_service, smart


def test_llm_split_preview_apply_save_reload(tmp_path: Path) -> None:
    manager, scene_service, smart = build_stack(tmp_path)
    project = manager.create_project("智能分镜", SCRIPT, "9:16", tmp_path / "out")

    # 1. LLM 拆分只产预览，不改项目
    preview = smart.split_with_llm(SCRIPT)
    assert preview == LLM_SPLIT
    assert project.scenes == []
    assert not scene_service.is_dirty

    # 2. 用户确认后经唯一 SceneService 应用
    scenes = scene_service.replace_from_texts(project, preview)
    assert [s.scene_id for s in scenes] == [1, 2, 3]
    assert all(s.status == "pending" for s in scenes)
    assert scene_service.is_dirty

    # 不变量
    assert normalize_for_comparison("".join(s.text for s in scenes)) == \
        normalize_for_comparison(clean_script(SCRIPT))

    # 3. 保存并重开
    scene_service.save(project)
    reloaded = manager.load_project(tmp_path / "out" / "智能分镜")
    assert [s.text for s in reloaded.scenes] == LLM_SPLIT
    assert reloaded.original_script == SCRIPT

    # 4. 已有场景时再次应用触发覆盖保护（测试要求 13）
    with pytest.raises(ScenesExistError):
        scene_service.replace_from_texts(reloaded, preview)
    scene_service.replace_from_texts(reloaded, preview, overwrite=True)


def test_preview_cancel_changes_nothing(tmp_path: Path) -> None:
    """测试要求 13/14：预览后取消，项目数据无任何改动。"""
    manager, scene_service, smart = build_stack(tmp_path)
    project = manager.create_project("取消预览", SCRIPT, "9:16", tmp_path / "out")
    saved_json = (tmp_path / "out" / "取消预览" / "project.json").read_text("utf-8")

    smart.split_with_llm(SCRIPT)  # 产生预览后用户取消：什么都不做

    assert project.scenes == []
    assert not scene_service.is_dirty
    assert (tmp_path / "out" / "取消预览" / "project.json").read_text("utf-8") == saved_json


def test_invalid_scenes_structure_leaves_project_untouched(tmp_path: Path) -> None:
    """结构验证失败：不修改项目、不产生 dirty（Groq 兼容修复的回归锚点）。"""
    config_store = ConfigStore(tmp_path / "config.json")
    secret_store = FakeSecretStore()
    config_store.save(LLMSettings(enabled=True, base_url=BASE_URL, model="m"))
    secret_store.set(secret_id_for_base_url(BASE_URL), "test-key")

    def bad_structure_handler(request: httpx.Request) -> httpx.Response:
        # 模拟 gpt-oss 类模型：数组项是对象而非字符串
        content = json.dumps(
            {"scenes": [{"scene": "第一段"}, {"scene": "第二段"}]},
            ensure_ascii=False,
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    def factory(settings: LLMSettings):
        client = OpenAICompatibleClient(
            base_url=settings.base_url,
            model=settings.model,
            secret_store=secret_store,
            max_retries=0,
            transport=httpx.MockTransport(bad_structure_handler),
            sleeper=lambda s: None,
        )
        return LLMSceneSplitter(client)

    manager = ProjectManager()
    rule_splitter = RuleBasedSceneSplitter()
    scene_service = SceneService(rule_splitter, manager)
    smart = SmartSplitService(config_store, secret_store, rule_splitter, factory)

    project = manager.create_project("结构失败", SCRIPT, "9:16", tmp_path / "out")
    saved = (tmp_path / "out" / "结构失败" / "project.json").read_text("utf-8")

    from auto_video_maker.providers.llm_scene_splitter import LLMSplitError

    with pytest.raises(LLMSplitError, match="格式不符合要求"):
        smart.split_with_llm(SCRIPT)

    assert project.scenes == []
    assert not scene_service.is_dirty
    assert (tmp_path / "out" / "结构失败" / "project.json").read_text("utf-8") == saved


def test_new_split_after_protocol_end_to_end(tmp_path: Path) -> None:
    """新协议 {"split_after": [...]} 的完整链路。"""
    config_store = ConfigStore(tmp_path / "config.json")
    secret_store = FakeSecretStore()
    config_store.save(LLMSettings(enabled=True, base_url=BASE_URL, model="m"))
    secret_store.set(secret_id_for_base_url(BASE_URL), "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"  # strict 优先
        content = json.dumps({"split_after": [0, 1]}, ensure_ascii=False)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    def factory(settings: LLMSettings):
        client = OpenAICompatibleClient(
            base_url=settings.base_url,
            model=settings.model,
            secret_store=secret_store,
            transport=httpx.MockTransport(handler),
            sleeper=lambda s: None,
        )
        return LLMSceneSplitter(client)

    rule_splitter = RuleBasedSceneSplitter()
    smart = SmartSplitService(config_store, secret_store, rule_splitter, factory)
    assert smart.split_with_llm(SCRIPT) == LLM_SPLIT


def test_llm_failure_then_rules_fallback_by_ui_choice(tmp_path: Path) -> None:
    """LLM 失败 → UI 选择改用规则拆分 → split_with_rules 可用。"""
    config_store = ConfigStore(tmp_path / "config.json")
    secret_store = FakeSecretStore()
    config_store.save(LLMSettings(enabled=True, base_url=BASE_URL, model="m"))
    secret_store.set(secret_id_for_base_url(BASE_URL), "test-key")

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    def factory(settings: LLMSettings):
        client = OpenAICompatibleClient(
            base_url=settings.base_url,
            model=settings.model,
            secret_store=secret_store,
            max_retries=0,
            transport=httpx.MockTransport(failing_handler),
            sleeper=lambda s: None,
        )
        return LLMSceneSplitter(client)

    rule_splitter = RuleBasedSceneSplitter()
    smart = SmartSplitService(config_store, secret_store, rule_splitter, factory)

    from auto_video_maker.providers.llm_client import LLMServerError

    with pytest.raises(LLMServerError):
        smart.split_with_llm(SCRIPT)
    # UI 收到错误后由用户选择规则拆分
    fallback = smart.split_with_rules(SCRIPT)
    assert fallback
    assert normalize_for_comparison("".join(fallback)) == \
        normalize_for_comparison(clean_script(SCRIPT))
