"""音频业务：内容寻址缓存、合成编排、TTS 隐私状态。

- 缓存键：canonical JSON（键排序）SHA-256 前 24 位十六进制
- 缓存命中必须经 AudioProbe 再验证
- 不写 Scene（写入经 SceneService.set_scene_audio）
- 批量生成的线程编排由场景页以链式单任务实现（写入保持在主线程），
  本服务提供纯业务方法与待生成清单
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from auto_video_maker.infrastructure.audio_probe import AudioProbe, AudioProbeError
from auto_video_maker.infrastructure.config import ConfigStore
from auto_video_maker.models.project import (
    DEFAULT_SPEECH_RATE,
    VALID_SPEECH_RATES,
    Project,
)
from auto_video_maker.providers.tts_provider import TTSProvider, resolve_voice_id
from auto_video_maker.services.project_manager import ProjectManager

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 1
CACHE_HASH_LENGTH = 24
AUDIO_DIR_NAME = "audio"
OUTPUT_FORMAT = "mp3"
CURRENT_TTS_NOTICE_VERSION = 1


class AudioServiceError(Exception):
    """配音生成失败。消息面向用户。"""


def audio_cache_key(
    provider_id: str, voice_id: str, rate: str, text: str,
    output_format: str = OUTPUT_FORMAT,
) -> str:
    """内容寻址缓存键：canonical JSON → SHA-256 前 24 位十六进制。"""
    payload = json.dumps(
        {
            "schema_version": CACHE_SCHEMA_VERSION,
            "provider": provider_id,
            "voice_id": voice_id,
            "rate": rate,
            "output_format": output_format,
            "text": text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:CACHE_HASH_LENGTH]


class AudioService:
    """场景配音生成与缓存。"""

    def __init__(
        self,
        tts_provider: TTSProvider,
        audio_probe: AudioProbe,
        project_manager: ProjectManager,
        config_store: ConfigStore,
    ) -> None:
        self._tts_provider = tts_provider
        self._audio_probe = audio_probe
        self._project_manager = project_manager
        self._config_store = config_store

    # ------------------------------------------------------------ 隐私

    def needs_privacy_confirmation(self) -> bool:
        """TTS 隐私确认状态（与 LLM 确认完全分离）。

        Provider 或 notice_version 变化时必须重新确认。
        """
        settings = self._config_store.load()
        return not (
            settings.tts_privacy_confirmed
            and settings.tts_privacy_provider == self._tts_provider.provider_id
            and settings.tts_privacy_notice_version == CURRENT_TTS_NOTICE_VERSION
        )

    def record_privacy_confirmation(self) -> None:
        settings = self._config_store.load()
        settings.tts_privacy_confirmed = True
        settings.tts_privacy_provider = self._tts_provider.provider_id
        settings.tts_privacy_notice_version = CURRENT_TTS_NOTICE_VERSION
        self._config_store.save(settings)
        logger.info("TTS 隐私确认已记录")

    # ------------------------------------------------------------ 生成

    def generate_for_scene(self, project: Project, index: int) -> tuple[str, float]:
        """为指定场景合成（或复用缓存）配音，返回 (相对路径, 时长秒)。

        不写 Scene；写入由调用方经 SceneService.set_scene_audio 完成。
        """
        if not 0 <= index < len(project.scenes):
            raise AudioServiceError("所选场景不存在。")
        scene = project.scenes[index]
        text = scene.text.strip()
        if not text:
            raise AudioServiceError(
                f"第 {scene.scene_id} 个场景文字为空，无法生成配音。"
            )
        voice_id = resolve_voice_id(project.settings.voice)
        rate = project.settings.speech_rate
        if rate not in VALID_SPEECH_RATES:  # 防御（模型层已回落）
            rate = DEFAULT_SPEECH_RATE

        key = audio_cache_key(self._tts_provider.provider_id, voice_id, rate, text)
        file_name = f"tts_{key}.{OUTPUT_FORMAT}"
        relative_path = f"{AUDIO_DIR_NAME}/{file_name}"
        project_root = self._project_manager.project_directory(project)
        audio_dir = project_root / AUDIO_DIR_NAME
        audio_dir.mkdir(parents=True, exist_ok=True)
        final_path = audio_dir / file_name

        # 缓存命中：必须再次通过 AudioProbe 验证
        if final_path.is_file():
            try:
                duration = self._audio_probe.duration_seconds(final_path)
                logger.info("配音缓存命中: %s", file_name)
                return relative_path, duration
            except AudioProbeError:
                logger.warning("配音缓存验证失败，重新合成: %s", file_name)

        self._tts_provider.synthesize(text, voice_id, rate, final_path)
        try:
            duration = self._audio_probe.duration_seconds(final_path)
        except AudioProbeError as exc:
            final_path.unlink(missing_ok=True)  # 不保留无效产物
            raise AudioServiceError(str(exc)) from exc
        return relative_path, duration

    def pending_indices(self, project: Project) -> list[int]:
        """返回尚无有效配音引用的场景下标（供批量生成）。"""
        return [
            i for i, scene in enumerate(project.scenes)
            if not scene.audio_path or not scene.duration
        ]
