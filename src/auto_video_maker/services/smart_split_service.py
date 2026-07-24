"""智能拆分服务：只执行拆分请求，返回预览用 list[str]。

职责边界（见 TASK.md）：
- split_with_llm / split_with_rules 两个独立方法
- 不弹窗、不等待用户输入、不自行决定回退
- 不创建 Scene、不修改 Project、不维护 dirty 状态
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Protocol

from auto_video_maker.infrastructure.config import (
    ConfigStore,
    LLMSettings,
    normalize_base_url,
)
from auto_video_maker.infrastructure.secret_store import (
    SecretStore,
    secret_id_for_base_url,
)
from auto_video_maker.services.scene_splitter import SceneSplitter
from auto_video_maker.services.script_parser import clean_script

logger = logging.getLogger(__name__)


class SmartSplitError(Exception):
    """智能拆分不可用或执行失败。消息面向用户。"""


@dataclass
class LLMAvailability:
    """智能拆分可用性检查结果。"""

    available: bool
    reason: str = ""


class LLMSplitterFactory(Protocol):
    """由 composition root（app.py）注入的 LLM 拆分器工厂。"""

    def __call__(self, settings: LLMSettings) -> SceneSplitter: ...


class SmartSplitService:
    """执行规则式或 LLM 拆分，产出仅供预览的场景文字列表。"""

    def __init__(
        self,
        config_store: ConfigStore,
        secret_store: SecretStore,
        rule_splitter: SceneSplitter,
        llm_splitter_factory: Callable[[LLMSettings], SceneSplitter],
    ) -> None:
        self._config_store = config_store
        self._secret_store = secret_store
        self._rule_splitter = rule_splitter
        self._llm_splitter_factory = llm_splitter_factory

    # ------------------------------------------------------------ 可用性

    def availability(self) -> LLMAvailability:
        """检查智能拆分可用条件：enabled、base_url、model、当前地址的 Key。"""
        settings = self._config_store.load()
        if not settings.enabled:
            return LLMAvailability(False, "智能分镜未启用。请在设置中开启。")
        if not settings.base_url.strip():
            return LLMAvailability(False, "未配置 Base URL。请在设置中填写。")
        if not settings.model.strip():
            return LLMAvailability(False, "未配置模型名称。请在设置中填写。")
        if not self._secret_store.exists(secret_id_for_base_url(settings.base_url)):
            return LLMAvailability(False, "API Key 未配置。请在设置中保存 Key。")
        return LLMAvailability(True)

    def current_settings(self) -> LLMSettings:
        """返回当前配置（供 UI 做只读判断）。"""
        return self._config_store.load()

    # ------------------------------------------------------------ 隐私确认

    def needs_privacy_confirmation(self) -> bool:
        """当前 base_url 是否需要（重新）进行隐私确认。

        隐私确认与规范化 base_url 绑定；地址改变后确认自动失效。
        """
        settings = self._config_store.load()
        normalized = normalize_base_url(settings.base_url)
        if not normalized:
            return True
        return settings.privacy_confirmed_for_base_url != normalized

    def record_privacy_confirmation(self) -> None:
        """记录用户对当前规范化 base_url 的隐私确认。"""
        settings = self._config_store.load()
        settings.privacy_confirmed_for_base_url = normalize_base_url(settings.base_url)
        self._config_store.save(settings)
        logger.info("隐私确认已记录")

    # ------------------------------------------------------------ 拆分

    def split_with_llm(self, text: str) -> list[str]:
        """经 LLM 拆分，返回预览用 list[str]。失败抛出异常，不自行回退。"""
        check = self.availability()
        if not check.available:
            raise SmartSplitError(check.reason)
        settings = self._config_store.load()
        splitter = self._llm_splitter_factory(settings)
        cleaned = clean_script(text)
        texts = splitter.split(cleaned)
        if not texts:
            raise SmartSplitError("文案中没有可拆分的内容。")
        return texts

    def split_with_rules(self, text: str) -> list[str]:
        """经规则式拆分，返回预览用 list[str]。"""
        cleaned = clean_script(text)
        texts = self._rule_splitter.split(cleaned)
        if not texts:
            raise SmartSplitError("文案中没有可拆分的内容。")
        return texts
