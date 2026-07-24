"""OpenAICompatibleClient 测试（httpx.MockTransport，不发真实请求）。"""

import json

import httpx
import pytest

from auto_video_maker.infrastructure.secret_store import (
    FakeSecretStore,
    secret_id_for_base_url,
)
from auto_video_maker.providers.llm_client import (
    LLMAuthError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMRequestError,
    LLMResponseError,
    LLMServerError,
    OpenAICompatibleClient,
    validate_base_url,
)

BASE_URL = "https://api.example.com/v1"
API_KEY = "sk-test-secret-key-0000"


def make_store(base_url: str = BASE_URL) -> FakeSecretStore:
    store = FakeSecretStore()
    store.set(secret_id_for_base_url(base_url), API_KEY)
    return store


def ok_response(content: str = "hello") -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}}]}
    )


def make_client(
    handler, base_url: str = BASE_URL, max_retries: int = 2, store=None
) -> tuple[OpenAICompatibleClient, list[float]]:
    sleeps: list[float] = []
    client = OpenAICompatibleClient(
        base_url=base_url,
        model="test-model",
        secret_store=store or make_store(base_url),
        timeout_seconds=5.0,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
        sleeper=sleeps.append,
    )
    return client, sleeps


class TestValidateBaseUrl:
    def test_https_accepted(self) -> None:
        assert validate_base_url(" https://API.example.com/v1/ ") == "https://api.example.com/v1"

    def test_http_non_loopback_rejected(self) -> None:
        with pytest.raises(LLMRequestError, match="HTTPS"):
            validate_base_url("http://api.example.com/v1")

    @pytest.mark.parametrize("url", [
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
    ])
    def test_http_loopback_allowed(self, url: str) -> None:
        validate_base_url(url)

    def test_userinfo_rejected(self) -> None:
        with pytest.raises(LLMRequestError, match="用户名或密码"):
            validate_base_url("https://user:pass@api.example.com/v1")

    def test_empty_rejected(self) -> None:
        with pytest.raises(LLMRequestError):
            validate_base_url("   ")


class TestHttpContract:
    def test_request_shape_and_response_parsing(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            captured["auth"] = request.headers.get("Authorization")
            captured["ua"] = request.headers.get("User-Agent")
            return ok_response("模型回复")

        client, _ = make_client(handler)
        result = client.send("提示词内容")

        assert result == "模型回复"
        assert captured["url"] == BASE_URL + "/chat/completions"
        assert captured["body"]["model"] == "test-model"
        assert captured["body"]["temperature"] == 0
        assert captured["body"]["messages"] == [
            {"role": "user", "content": "提示词内容"}
        ]
        assert captured["auth"] == f"Bearer {API_KEY}"
        assert "AutoVideoMaker" in captured["ua"]

    def test_missing_key_raises_without_request(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return ok_response()

        client, _ = make_client(handler, store=FakeSecretStore())
        with pytest.raises(LLMAuthError, match="API Key 未配置"):
            client.send("x")
        assert calls == []

    def test_malformed_success_body(self) -> None:
        client, _ = make_client(lambda r: httpx.Response(200, json={"oops": 1}))
        with pytest.raises(LLMResponseError):
            client.send("x")


class TestRetryPolicy:
    def test_400_and_401_no_retry(self) -> None:
        for status, exc_type in ((400, LLMRequestError), (401, LLMAuthError)):
            calls = []

            def handler(request: httpx.Request) -> httpx.Response:
                calls.append(1)
                return httpx.Response(status)

            client, _ = make_client(handler, max_retries=3)
            with pytest.raises(exc_type):
                client.send("x")
            assert len(calls) == 1  # 总尝试次数 1，不重试

    def test_429_retries_and_honors_retry_after(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) < 3:
                return httpx.Response(429, headers={"Retry-After": "2"})
            return ok_response("成功")

        client, sleeps = make_client(handler, max_retries=2)
        assert client.send("x") == "成功"
        assert len(calls) == 3
        assert sleeps == [2.0, 2.0]

    def test_5xx_retries_up_to_total_attempts(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(503)

        client, _ = make_client(handler, max_retries=2)
        with pytest.raises(LLMServerError):
            client.send("x")
        assert len(calls) == 3  # 总尝试次数 = 1 + max_retries

    def test_timeout_retries(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            raise httpx.ConnectTimeout("timeout")

        client, _ = make_client(handler, max_retries=1)
        with pytest.raises(LLMNetworkError):
            client.send("x")
        assert len(calls) == 2

    def test_network_error_retries(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            raise httpx.ConnectError("no network")

        client, _ = make_client(handler, max_retries=2)
        with pytest.raises(LLMNetworkError):
            client.send("x")
        assert len(calls) == 3

    def test_rate_limit_exhausted(self) -> None:
        client, _ = make_client(lambda r: httpx.Response(429), max_retries=1)
        with pytest.raises(LLMRateLimitError):
            client.send("x")


class TestSecurityRules:
    def test_redirect_not_followed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "https://evil.example.com"})

        client, _ = make_client(handler)
        with pytest.raises(LLMRequestError, match="重定向"):
            client.send("x")

    def test_http_base_url_rejected_at_construction(self) -> None:
        with pytest.raises(LLMRequestError):
            OpenAICompatibleClient(
                base_url="http://api.example.com/v1",
                model="m",
                secret_store=FakeSecretStore(),
            )

    def test_userinfo_base_url_rejected(self) -> None:
        with pytest.raises(LLMRequestError):
            OpenAICompatibleClient(
                base_url="https://u:p@api.example.com/v1",
                model="m",
                secret_store=FakeSecretStore(),
            )

    def test_empty_model_rejected(self) -> None:
        with pytest.raises(LLMRequestError):
            OpenAICompatibleClient(
                base_url=BASE_URL, model="  ", secret_store=FakeSecretStore()
            )

    def test_key_never_in_error_messages_or_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        client, _ = make_client(handler)
        with caplog.at_level("DEBUG"):
            with pytest.raises(LLMAuthError) as exc_info:
                client.send("x")
        # Key 及其片段不得出现在异常与日志中
        assert API_KEY not in str(exc_info.value)
        assert API_KEY[:8] not in str(exc_info.value)
        for record in caplog.records:
            message = record.getMessage()
            assert API_KEY not in message
            assert API_KEY[:8] not in message
