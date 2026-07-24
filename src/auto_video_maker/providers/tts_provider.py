"""TTS：TTSProvider 接口与 edge-tts 默认实现。

- UI 不得直接调用 edge-tts，必须经本接口（既定决议）
- 语音映射只在 Provider 内进行
- .part 临时文件 + 成功后原子移动；失败与取消不留残余
- 日志不得记录完整文案或完整服务响应
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

EDGE_TTS_PROVIDER_ID = "edge-tts"

# 语音映射（模型存储值 → edge-tts voice_id）
_VOICE_MAP = {
    "female": "zh-CN-XiaoxiaoNeural",
    "male": "zh-CN-YunxiNeural",
    "default": "zh-CN-XiaoxiaoNeural",  # 向后兼容：default → female
}


class TTSProviderError(Exception):
    """TTS 合成失败（不可重试类）。消息面向用户。"""


class TTSNetworkError(TTSProviderError):
    """网络错误 / 超时 / 服务暂时异常。可重试。"""


@dataclass
class TTSVoice:
    """可用语音描述。"""

    voice_id: str
    display_name: str
    gender: str


def resolve_voice_id(voice: str) -> str:
    """将模型存储值解析为完整 voice_id；非法值安全回落 female。"""
    voice_id = _VOICE_MAP.get(voice)
    if voice_id is None:
        logger.warning("未知语音值已回落为女声: %r", voice)
        return _VOICE_MAP["female"]
    return voice_id


class TTSProvider(ABC):
    """TTS 统一接口（遵循 ARCHITECTURE 4.6）。"""

    provider_id: str = ""

    @abstractmethod
    def list_voices(self) -> list[TTSVoice]:
        """返回可用语音列表。"""

    @abstractmethod
    def synthesize(self, text: str, voice_id: str, rate: str, output_path: Path) -> None:
        """将文字合成为 mp3 写入 output_path。失败抛出 TTSProviderError 子类。"""


class EdgeTTSProvider(TTSProvider):
    """edge-tts 实现（MVP 默认；微软在线语音服务）。

    异步库在调用线程内以 asyncio.run 包装（应在 TaskRunner 工作线程中
    调用），不创建全局事件循环。
    """

    provider_id = EDGE_TTS_PROVIDER_ID

    def __init__(
        self,
        max_retries: int = 2,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max_retries = max(0, int(max_retries))
        self._sleep = sleeper

    def list_voices(self) -> list[TTSVoice]:
        return [
            TTSVoice("zh-CN-XiaoxiaoNeural", "女声（晓晓）", "female"),
            TTSVoice("zh-CN-YunxiNeural", "男声（云希）", "male"),
        ]

    def synthesize(self, text: str, voice_id: str, rate: str, output_path: Path) -> None:
        if not text.strip():
            raise TTSProviderError("场景文字为空，无法生成配音。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = output_path.with_suffix(output_path.suffix + ".part")
        total_attempts = 1 + self._max_retries
        last_error: TTSProviderError = TTSNetworkError("配音生成失败，请稍后重试。")
        try:
            for attempt in range(1, total_attempts + 1):
                try:
                    asyncio.run(self._synthesize_once(text, voice_id, rate, part_path))
                except TTSProviderError as exc:
                    part_path.unlink(missing_ok=True)
                    if not isinstance(exc, TTSNetworkError):
                        raise
                    last_error = exc
                    if attempt < total_attempts:
                        self._sleep(0.5 * attempt)
                    continue
                if not part_path.is_file() or part_path.stat().st_size == 0:
                    part_path.unlink(missing_ok=True)
                    last_error = TTSNetworkError("语音服务返回了空音频，稍后会自动重试。")
                    if attempt < total_attempts:
                        self._sleep(0.5 * attempt)
                    continue
                part_path.replace(output_path)  # 原子移动
                logger.info(
                    "配音合成完成 (provider=%s, voice=%s, 文字长度=%d)",
                    self.provider_id, voice_id, len(text),
                )
                return
            raise last_error
        finally:
            part_path.unlink(missing_ok=True)

    # ------------------------------------------------------------ 内部

    @staticmethod
    async def _synthesize_once(
        text: str, voice_id: str, rate: str, part_path: Path
    ) -> None:
        import edge_tts  # 延迟导入，避免测试环境强依赖

        try:
            communicate = edge_tts.Communicate(text, voice_id, rate=rate)
            await communicate.save(str(part_path))
        except (asyncio.TimeoutError, OSError, ConnectionError) as exc:
            raise TTSNetworkError(
                "无法连接语音服务。请检查网络后重试。"
            ) from exc
        except ValueError as exc:
            raise TTSProviderError(
                "语音参数或文字无效，无法生成配音。"
            ) from exc
        except Exception as exc:  # noqa: BLE001 aiohttp/edge-tts 的其余异常按网络类处理
            raise TTSNetworkError(
                "语音服务暂时不可用。请稍后重试。"
            ) from exc
