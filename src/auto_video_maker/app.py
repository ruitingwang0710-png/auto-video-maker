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
from auto_video_maker.infrastructure.logging_config import (
    default_log_path,
    setup_logging,
)
from auto_video_maker.infrastructure.secret_store import (
    InMemorySecretStore,
    MacOSKeychainSecretStore,
    SecretStore,
)
from auto_video_maker.infrastructure.task_runner import TaskRunner
from auto_video_maker.infrastructure import resource_locator
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
    setup_logging(log_file=default_log_path())
    logger.info("应用启动: %s（frozen=%s）", APP_NAME, resource_locator.is_frozen())
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

    # frozen 时优先使用应用内 Contents/MacOS/bin 的捆绑 FFmpeg
    ffmpeg_runner = FFmpegRunner(
        config_store=config_store,
        app_bin_dir=resource_locator.bundled_bin_dir(),
    )
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

    # 首次启动检查：QApplication 创建后、进入事件循环时执行；
    # 只提示一次，不阻止进入首页（约束 D）
    def _run_startup_checks() -> None:
        from PySide6.QtWidgets import QMessageBox

        from auto_video_maker.services.startup_checks import run_startup_checks

        issues = run_startup_checks(ffmpeg_runner, config_store)
        if issues:
            QMessageBox.information(
                window,
                "启动检查提示",
                "\n\n".join(issue.message for issue in issues),
            )

    from PySide6.QtCore import QTimer

    QTimer.singleShot(0, _run_startup_checks)
    exit_code = app.exec()
    logger.info("应用退出，退出码 %s", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
