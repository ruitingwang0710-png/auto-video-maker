"""LLM 客户端：LLMClient 接口与 OpenAI Chat Completions 兼容实现。

HTTP 合约（见 TASK.md）：
- base_url 为 API 根地址（例如 https://example.com/v1）
- 请求地址：base_url + /chat/completions
- 响应从 choices[0].message.content 读取
- 不跟随重定向；非回环地址强制 HTTPS；URL 禁止用户名密码
- API Key 只进入 Authorization 请求头，绝不进入日志与异常信息
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Callable
from urllib.parse import urlsplit

import httpx

from auto_video_maker import __version__
from auto_video_maker.infrastructure.config import normalize_base_url
from auto_video_maker.infrastructure.secret_store import SecretStore, secret_id_for_base_url

logger = logging.getLogger(__name__)

USER_AGENT = f"AutoVideoMaker/{__version__}"
CHAT_COMPLETIONS_PATH = "/chat/completions"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class LLMClientError(Exception):
    """LLM 调用失败。消息面向用户，绝不包含 API Key。"""


class LLMRequestError(LLMClientError):
    """400：配置或请求有误。不重试。"""


class LLMAuthError(LLMClientError):
    """401：API Key 验证失败。不重试。"""


class LLMRateLimitError(LLMClientError):
    """429：请求过于频繁。有限重试。"""


class LLMServerError(LLMClientError):
    """5xx：服务暂时异常。有限重试。"""


class LLMNetworkError(LLMClientError):
    """网络错误或超时。有限重试。"""


class LLMResponseError(LLMClientError):
    """响应格式无法解析。不自动重试。"""


class LLMClient(ABC):
    """LLM 客户端统一接口。"""

    @abstractmethod
    def send(self, prompt: str, response_format: dict | None = None) -> str:
        """发送提示词，返回模型原始文本。失败抛出 LLMClientError 子类。

        response_format：可选的 OpenAI 兼容结构化输出约束
        （如 json_schema / json_object）。
        """


class _ResponseFormatUnsupported(Exception):
    """内部信号：服务明确拒绝了当前 response_format（触发受控回退）。"""


def validate_base_url(base_url: str) -> str:
    """校验并返回规范化 base_url。

    - 必须为 http/https 且含主机
    - URL 中禁止包含用户名和密码
    - 非回环地址必须使用 HTTPS
    """
    normalized = normalize_base_url(base_url)
    if not normalized:
        raise LLMRequestError("Base URL 未配置。")
    parts = urlsplit(normalized)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise LLMRequestError("Base URL 无效：必须是 http(s) 地址。")
    if parts.username or parts.password:
        raise LLMRequestError("Base URL 中不允许包含用户名或密码。")
    if parts.scheme == "http" and parts.hostname not in LOOPBACK_HOSTS:
        raise LLMRequestError("非本机地址必须使用 HTTPS。")
    return normalized


class OpenAICompatibleClient(LLMClient):
    """OpenAI Chat Completions 兼容客户端（MVP 第一个实现）。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        secret_store: SecretStore,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = validate_base_url(base_url)
        self._host = urlsplit(self._base_url).hostname or ""
        if not model.strip():
            raise LLMRequestError("模型名称未配置。")
        self._model = model.strip()
        self._secret_store = secret_store
        self._secret_id = secret_id_for_base_url(self._base_url)
        self._timeout = timeout_seconds
        self._max_retries = max(0, int(max_retries))
        self._transport = transport
        self._sleep = sleeper

    def send(self, prompt: str, response_format: dict | None = None) -> str:
        api_key = self._secret_store.get(self._secret_id)
        if not api_key:
            raise LLMAuthError("API Key 未配置。请在设置中配置后重试。")

        tiers = self._response_format_tiers(response_format)
        for index, tier in enumerate(tiers):
            try:
                return self._send_with_retries(prompt, api_key, tier)
            except _ResponseFormatUnsupported:
                if index + 1 < len(tiers):
                    # 受控回退：json_schema → json_object（仅此一档）
                    logger.warning(
                        "模型服务不支持当前结构化输出，回退下一档 "
                        "(host=%s, model=%s)",
                        self._host,
                        self._model,
                    )
                    continue
                raise LLMRequestError(
                    "模型服务不支持结构化输出（response_format）。"
                    "请更换模型，或改用规则拆分。"
                )
        raise LLMRequestError("请求未能完成。")  # 理论不可达

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _response_format_tiers(response_format: dict | None) -> list[dict | None]:
        """结构化输出层级：strict json_schema 优先，受控回退 json_object。"""
        if response_format is None:
            return [None]
        if response_format.get("type") == "json_schema":
            return [response_format, {"type": "json_object"}]
        return [response_format]

    def _send_with_retries(
        self, prompt: str, api_key: str, response_format: dict | None
    ) -> str:
        url = self._base_url + CHAT_COMPLETIONS_PATH
        body: dict = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        if response_format is not None:
            body["response_format"] = response_format
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        }
        total_attempts = 1 + self._max_retries
        last_error: LLMClientError = LLMNetworkError("网络请求失败。")

        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            for attempt in range(1, total_attempts + 1):
                try:
                    response = client.post(url, json=body, headers=headers)
                except httpx.TimeoutException:
                    last_error = LLMNetworkError(
                        "请求超时。请检查网络后重试，或改用规则拆分。"
                    )
                except httpx.TransportError:
                    last_error = LLMNetworkError(
                        "网络连接失败。请检查网络后重试，或改用规则拆分。"
                    )
                else:
                    if self._is_response_format_rejection(response, response_format):
                        raise _ResponseFormatUnsupported()
                    outcome = self._classify_response(response)
                    if isinstance(outcome, str):
                        return outcome
                    last_error = outcome
                    if not self._is_retryable(outcome):
                        logger.info(
                            "LLM 请求失败 (host=%s, model=%s, status=%s)",
                            self._host,
                            self._model,
                            response.status_code,
                        )
                        raise outcome
                    self._wait_before_retry(attempt, total_attempts, response)
                    continue
                # 网络类错误的重试等待
                if attempt < total_attempts:
                    self._wait_before_retry(attempt, total_attempts, None)
        raise last_error

    @staticmethod
    def _is_response_format_rejection(
        response: httpx.Response, response_format: dict | None
    ) -> bool:
        """服务是否明确拒绝了 response_format（仅在使用了它的 400 上判定）。

        只检查错误体是否点名 response_format / json_schema，
        不无条件吞掉所有 400。
        """
        if response_format is None or response.status_code != 400:
            return False
        try:
            body_text = response.text[:2000].lower()
        except Exception:  # noqa: BLE001 防御：读取失败按普通 400 处理
            return False
        return "response_format" in body_text or "json_schema" in body_text

    @staticmethod
    def _is_retryable(error: LLMClientError) -> bool:
        return isinstance(error, (LLMRateLimitError, LLMServerError, LLMNetworkError))

    def _wait_before_retry(
        self, attempt: int, total_attempts: int, response: httpx.Response | None
    ) -> None:
        if attempt >= total_attempts:
            return
        delay = 0.5 * attempt
        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    delay = max(0.0, min(float(retry_after), 30.0))
                except ValueError:
                    pass
        self._sleep(delay)

    def _classify_response(self, response: httpx.Response) -> str | LLMClientError:
        status = response.status_code
        if 300 <= status < 400:
            return LLMRequestError(
                "服务返回了重定向。出于安全考虑不跟随重定向，请检查 Base URL。"
            )
        if status == 400:
            return LLMRequestError("请求被服务拒绝（400）。请检查模型名称和配置。")
        if status == 401:
            logger.warning("API Key 验证失败")
            return LLMAuthError("API Key 验证失败。请在设置中检查 Key。")
        if status == 429:
            return LLMRateLimitError("请求过于频繁（429）。稍后会自动重试。")
        if 500 <= status < 600:
            return LLMServerError(f"模型服务暂时异常（{status}）。稍后会自动重试。")
        if status != 200:
            return LLMRequestError(f"模型服务返回了意外状态（{status}）。")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            return LLMResponseError("无法解析模型服务的响应格式。")
        if not isinstance(content, str):
            return LLMResponseError("模型服务返回的内容不是文本。")
        return content
