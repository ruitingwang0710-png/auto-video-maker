"""密钥存储：SecretStore 抽象接口与 macOS 钥匙串实现。

安全要求（见 TASK.md）：
- API Key 按规范化 base_url 生成的 secret_id 分开保存
- Key 不得出现在 subprocess 命令参数、环境变量、日志、异常信息中
- 本模块的日志与异常绝不包含密钥内容
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from abc import ABC, abstractmethod

from auto_video_maker.infrastructure.config import normalize_base_url

logger = logging.getLogger(__name__)

KEYCHAIN_SERVICE_NAME = "AutoVideoMaker-LLM"


class SecretStoreError(Exception):
    """密钥存取失败。消息面向用户，绝不包含密钥内容。"""


def secret_id_for_base_url(base_url: str) -> str:
    """由规范化 base_url 生成 secret_id（SHA-256 十六进制，不含 API Key）。"""
    normalized = normalize_base_url(base_url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class SecretStore(ABC):
    """按 secret_id 存取密钥的抽象接口。"""

    @abstractmethod
    def get(self, secret_id: str) -> str | None:
        """读取密钥；不存在时返回 None。"""

    @abstractmethod
    def set(self, secret_id: str, secret: str) -> None:
        """保存（或替换）密钥。"""

    @abstractmethod
    def delete(self, secret_id: str) -> None:
        """删除密钥；不存在时静默成功。"""

    @abstractmethod
    def exists(self, secret_id: str) -> bool:
        """密钥是否存在。"""


class InMemorySecretStore(SecretStore):
    """内存实现：用于测试（FakeSecretStore）与非 macOS 开发环境。

    注意：不做持久化，应用退出后丢失。
    """

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def get(self, secret_id: str) -> str | None:
        return self._secrets.get(secret_id)

    def set(self, secret_id: str, secret: str) -> None:
        self._secrets[secret_id] = secret

    def delete(self, secret_id: str) -> None:
        self._secrets.pop(secret_id, None)

    def exists(self, secret_id: str) -> bool:
        return secret_id in self._secrets


# 测试中使用的别名（见 TASK.md）
FakeSecretStore = InMemorySecretStore


def _escape_for_security_interactive(value: str) -> str:
    """转义 security -i 交互命令中的双引号字符串内容。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class MacOSKeychainSecretStore(SecretStore):
    """macOS 钥匙串实现。

    通过 `security -i` 交互模式执行命令：完整命令（含密钥）经 stdin 传入，
    密钥不出现在 subprocess 命令参数或环境变量中。
    读取时密钥经管道返回，不打印到本进程的 stdout/stderr，不写日志。
    """

    def __init__(self, service_name: str = KEYCHAIN_SERVICE_NAME) -> None:
        self._service = service_name

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _require_security_binary() -> str:
        binary = shutil.which("security")
        if not binary:
            raise SecretStoreError(
                "当前系统不支持 macOS 钥匙串（未找到 security 命令）。"
            )
        return binary

    def _run_interactive(self, command: str) -> subprocess.CompletedProcess[bytes]:
        """经 stdin 执行 security 交互命令（密钥不进入 argv/env）。"""
        binary = self._require_security_binary()
        return subprocess.run(
            [binary, "-i"],
            input=(command + "\n").encode("utf-8"),
            capture_output=True,
            timeout=15,
            check=False,
        )

    def _run_args(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        """执行不含密钥内容的 security 子命令。"""
        binary = self._require_security_binary()
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            timeout=15,
            check=False,
        )

    # ------------------------------------------------------------ 接口实现

    def get(self, secret_id: str) -> str | None:
        result = self._run_args(
            "find-generic-password",
            "-s", self._service,
            "-a", secret_id,
            "-w",
        )
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8").rstrip("\n")

    def set(self, secret_id: str, secret: str) -> None:
        if not secret:
            raise SecretStoreError("不能保存空的 API Key。")
        command = (
            f'add-generic-password -U -s "{_escape_for_security_interactive(self._service)}" '
            f'-a "{_escape_for_security_interactive(secret_id)}" '
            f'-w "{_escape_for_security_interactive(secret)}"'
        )
        result = self._run_interactive(command)
        if result.returncode != 0:
            # 绝不将 stderr 内容并入异常（避免任何意外泄漏）
            raise SecretStoreError("写入 macOS 钥匙串失败。")
        logger.info("API Key 已配置")

    def delete(self, secret_id: str) -> None:
        result = self._run_args(
            "delete-generic-password",
            "-s", self._service,
            "-a", secret_id,
        )
        # 不存在（returncode!=0 中的 item not found）视为静默成功
        if result.returncode == 0:
            logger.info("API Key 已删除")

    def exists(self, secret_id: str) -> bool:
        result = self._run_args(
            "find-generic-password",
            "-s", self._service,
            "-a", secret_id,
        )
        return result.returncode == 0
