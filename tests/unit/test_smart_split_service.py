"""SmartSplitService 测试：可用性、隐私确认、只产预览不改数据。"""

from pathlib import Path

import pytest

from auto_video_maker.infrastructure.config import ConfigStore, LLMSettings
from auto_video_maker.infrastructure.secret_store import (
    FakeSecretStore,
    secret_id_for_base_url,
)
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.scene_splitter import RuleBasedSceneSplitter, SceneSplitter
from auto_video_maker.services.smart_split_service import (
    SmartSplitError,
    SmartSplitService,
)

BASE_URL = "https://api.example.com/v1"
SCRIPT = "人工智能正在改变企业的工作方式。现在，自动化工具可以协助企业处理重复任务。"


class FakeSplitter(SceneSplitter):
    def __init__(self, result: list[str] | None = None, error: Exception | None = None):
        self.result = result or []
        self.error = error
        self.calls: list[str] = []

    def split(self, cleaned_script: str) -> list[str]:
        self.calls.append(cleaned_script)
        if self.error:
            raise self.error
        return list(self.result)


@pytest.fixture
def config_store(tmp_path: Path) -> ConfigStore:
    return ConfigStore(tmp_path / "config.json")


@pytest.fixture
def secret_store() -> FakeSecretStore:
    return FakeSecretStore()


def make_service(
    config_store: ConfigStore,
    secret_store: FakeSecretStore,
    llm_splitter: SceneSplitter | None = None,
) -> SmartSplitService:
    factory_calls: list = []

    def factory(settings: LLMSettings) -> SceneSplitter:
        factory_calls.append(settings)
        return llm_splitter or FakeSplitter(["场景一。", "场景二。"])

    service = SmartSplitService(
        config_store, secret_store, RuleBasedSceneSplitter(), factory
    )
    service._test_factory_calls = factory_calls  # type: ignore[attr-defined]
    return service


def configure_ready(config_store: ConfigStore, secret_store: FakeSecretStore) -> None:
    config_store.save(
        LLMSettings(enabled=True, base_url=BASE_URL, model="test-model")
    )
    secret_store.set(secret_id_for_base_url(BASE_URL), "real-key")


# ------------------------------------------------------------ 可用性（测试要求 21 部分）

class TestAvailability:
    def test_available_when_all_conditions_met(self, config_store, secret_store) -> None:
        configure_ready(config_store, secret_store)
        service = make_service(config_store, secret_store)
        assert service.availability().available

    def test_disabled(self, config_store, secret_store) -> None:
        configure_ready(config_store, secret_store)
        settings = config_store.load()
        settings.enabled = False
        config_store.save(settings)
        check = make_service(config_store, secret_store).availability()
        assert not check.available
        assert "未启用" in check.reason

    def test_missing_base_url(self, config_store, secret_store) -> None:
        config_store.save(LLMSettings(enabled=True, base_url="", model="m"))
        check = make_service(config_store, secret_store).availability()
        assert not check.available
        assert "Base URL" in check.reason

    def test_missing_model(self, config_store, secret_store) -> None:
        config_store.save(LLMSettings(enabled=True, base_url=BASE_URL, model=" "))
        check = make_service(config_store, secret_store).availability()
        assert not check.available

    def test_missing_key_for_current_base_url(self, config_store, secret_store) -> None:
        config_store.save(LLMSettings(enabled=True, base_url=BASE_URL, model="m"))
        check = make_service(config_store, secret_store).availability()
        assert not check.available
        assert "API Key 未配置" in check.reason

    def test_switching_base_url_does_not_reuse_old_key(
        self, config_store, secret_store
    ) -> None:
        """测试要求 18：切换 base_url 不误用旧 Key。"""
        configure_ready(config_store, secret_store)
        service = make_service(config_store, secret_store)
        assert service.availability().available
        # 切到新地址：钥匙串里只有旧地址的 Key → 不可用（未配置）
        settings = config_store.load()
        settings.base_url = "https://another.example.com/v1"
        config_store.save(settings)
        check = service.availability()
        assert not check.available
        assert "API Key 未配置" in check.reason
        # 切回旧地址：恢复可用
        settings.base_url = BASE_URL
        config_store.save(settings)
        assert service.availability().available


# ------------------------------------------------------------ 拆分（测试要求 9）

class TestSplitting:
    def test_split_with_llm_returns_preview(self, config_store, secret_store) -> None:
        configure_ready(config_store, secret_store)
        llm = FakeSplitter(["预览场景一。", "预览场景二。"])
        service = make_service(config_store, secret_store, llm)
        result = service.split_with_llm(SCRIPT)
        assert result == ["预览场景一。", "预览场景二。"]
        # 收到的是清理后的文案
        assert llm.calls == [SCRIPT]

    def test_split_with_llm_unavailable_raises(self, config_store, secret_store) -> None:
        service = make_service(config_store, secret_store)
        with pytest.raises(SmartSplitError):
            service.split_with_llm(SCRIPT)

    def test_split_with_llm_failure_propagates_without_fallback(
        self, config_store, secret_store
    ) -> None:
        configure_ready(config_store, secret_store)
        llm = FakeSplitter(error=RuntimeError("模型失败"))
        service = make_service(config_store, secret_store, llm)
        with pytest.raises(RuntimeError):
            service.split_with_llm(SCRIPT)  # 不自行回退

    def test_split_with_rules_independent(self, config_store, secret_store) -> None:
        service = make_service(config_store, secret_store)
        result = service.split_with_rules(SCRIPT)
        assert result  # 无需任何 LLM 配置即可用

    def test_split_does_not_touch_project_or_dirty(
        self, config_store, secret_store, tmp_path: Path
    ) -> None:
        configure_ready(config_store, secret_store)
        manager = ProjectManager()
        project = manager.create_project("独立性", SCRIPT, "9:16", tmp_path / "out")
        scene_service = SceneService(RuleBasedSceneSplitter(), manager)
        service = make_service(config_store, secret_store)

        service.split_with_llm(SCRIPT)
        service.split_with_rules(SCRIPT)

        assert project.scenes == []
        assert not scene_service.is_dirty


# ------------------------------------------------------------ 隐私确认（测试要求 22、23）

class TestPrivacyConfirmation:
    def test_needs_confirmation_initially(self, config_store, secret_store) -> None:
        configure_ready(config_store, secret_store)
        service = make_service(config_store, secret_store)
        assert service.needs_privacy_confirmation()

    def test_confirmation_recorded_for_normalized_url(
        self, config_store, secret_store
    ) -> None:
        configure_ready(config_store, secret_store)
        service = make_service(config_store, secret_store)
        service.record_privacy_confirmation()
        assert not service.needs_privacy_confirmation()
        assert config_store.load().privacy_confirmed_for_base_url == BASE_URL

    def test_same_url_different_formatting_no_reprompt(
        self, config_store, secret_store
    ) -> None:
        configure_ready(config_store, secret_store)
        service = make_service(config_store, secret_store)
        service.record_privacy_confirmation()
        settings = config_store.load()
        settings.base_url = "  HTTPS://API.Example.com/v1/  "  # 同一地址不同写法
        config_store.save(settings)
        assert not service.needs_privacy_confirmation()

    def test_changed_base_url_invalidates_confirmation(
        self, config_store, secret_store
    ) -> None:
        configure_ready(config_store, secret_store)
        service = make_service(config_store, secret_store)
        service.record_privacy_confirmation()
        settings = config_store.load()
        settings.base_url = "https://other.example.com/v1"
        config_store.save(settings)
        assert service.needs_privacy_confirmation()
