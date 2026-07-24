"""应用入口。

本地启动：
    python -m auto_video_maker.app
或安装后：
    auto-video-maker
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from auto_video_maker.infrastructure.config import ConfigStore, LLMSettings
from auto_video_maker.infrastructure.logging_config import setup_logging
from auto_video_maker.infrastructure.secret_store import (
    InMemorySecretStore,
    MacOSKeychainSecretStore,
    SecretStore,
)
from auto_video_maker.infrastructure.task_runner import TaskRunner
from auto_video_maker.infrastructure.audio_probe import AudioProbe
from auto_video_maker.infrastructure.ffmpeg_runner import FFmpegRunner
from auto_video_maker.providers.image_provider import OpenverseImageProvider
from auto_video_maker.providers.llm_client import LLMClient, OpenAICompatibleClient
from auto_video_maker.providers.llm_scene_splitter import LLMSceneSplitter
from auto_video_maker.providers.tts_provider import EdgeTTSProvider
from auto_video_maker.services.asset_download_service import AssetDownloadService
from auto_video_maker.services.audio_service import AudioService
from auto_video_maker.services.keyword_service import KeywordService
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.credits_service import CreditsService
from auto_video_maker.services.subtitle_service import SubtitleService
from auto_video_maker.services.video_render_service import VideoRenderService
from auto_video_maker.services.scene_service import SceneService
from auto_video_maker.services.scene_splitter import RuleBasedSceneSplitter, SceneSplitter
from auto_video_maker.services.smart_split_service import SmartSplitService
from auto_video_maker.ui.main_window import APP_NAME, MainWindow

logger = logging.getLogger(__name__)


def _create_secret_store() -> SecretStore:
    """按平台选择密钥存储。

    macOS 使用钥匙串；其他平台（仅开发环境）使用内存实现，不做持久化。
    """
    if sys.platform == "darwin":
        return MacOSKeychainSecretStore()
    logger.warning("非 macOS 平台：API Key 使用内存存储，应用退出后失效（仅供开发）")
    return InMemorySecretStore()


def main() -> int:
    """启动桌面应用，返回退出码。

    本函数是唯一的 composition root：所有服务与拆分器在此创建并注入。
    """
    setup_logging()
    logger.info("应用启动: %s", APP_NAME)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    project_manager = ProjectManager()
    rule_splitter = RuleBasedSceneSplitter()
    scene_service = SceneService(rule_splitter, project_manager)

    config_store = ConfigStore()
    secret_store = _create_secret_store()

    def llm_client_factory(settings: LLMSettings) -> LLMClient:
        return OpenAICompatibleClient(
            base_url=settings.base_url,
            model=settings.model,
            secret_store=secret_store,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )

    def llm_splitter_factory(settings: LLMSettings) -> SceneSplitter:
        return LLMSceneSplitter(llm_client_factory(settings))

    smart_split_service = SmartSplitService(
        config_store, secret_store, rule_splitter, llm_splitter_factory
    )
    task_runner = TaskRunner()

    image_provider = OpenverseImageProvider()
    download_service = AssetDownloadService()
    keyword_service = KeywordService(
        config_store,
        availability_check=smart_split_service.availability,
        llm_client_factory=llm_client_factory,
    )

    tts_provider = EdgeTTSProvider()
    audio_probe = AudioProbe()
    audio_service = AudioService(
        tts_provider, audio_probe, project_manager, config_store
    )
    subtitle_service = SubtitleService()

    ffmpeg_runner = FFmpegRunner(config_store=config_store)
    credits_service = CreditsService()
    render_service = VideoRenderService(
        ffmpeg_runner, subtitle_service, credits_service,
        project_manager, scene_service,
    )

    window = MainWindow(
        project_manager,
        scene_service,
        smart_split_service=smart_split_service,
        config_store=config_store,
        secret_store=secret_store,
        task_runner=task_runner,
        image_provider=image_provider,
        download_service=download_service,
        keyword_service=keyword_service,
        audio_service=audio_service,
        subtitle_service=subtitle_service,
        render_service=render_service,
    )
    window.show()
    exit_code = app.exec()
    logger.info("应用退出，退出码 %s", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
