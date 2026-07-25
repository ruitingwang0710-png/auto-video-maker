"""首次启动检查：FFmpeg 可用性与配置目录可写。

- 必须在 QApplication 创建后由 app.py 调用，不得在 import 时执行
- 不主动联网；不触发 LLM/Openverse/edge-tts
- 不记录环境变量、用户项目内容或任何密钥
- 检查失败只提示，不阻止进入首页；FFmpeg 不可用时
  既有本地非导出功能仍可使用
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from auto_video_maker.infrastructure.config import ConfigStore
from auto_video_maker.infrastructure.ffmpeg_runner import (
    FFmpegCapabilityError,
    FFmpegError,
    FFmpegNotFoundError,
    FFmpegRunner,
)

logger = logging.getLogger(__name__)


@dataclass
class StartupIssue:
    """一条启动检查问题（面向用户的说明文字）。"""

    category: str  # "ffmpeg" | "config"
    message: str


def run_startup_checks(
    ffmpeg_runner: FFmpegRunner, config_store: ConfigStore
) -> list[StartupIssue]:
    """执行启动检查，返回问题列表（空 = 全部通过）。"""
    issues: list[StartupIssue] = []

    try:
        ffmpeg_runner.check_capabilities(require_subtitles=True)
    except FFmpegNotFoundError as exc:
        issues.append(StartupIssue(
            "ffmpeg",
            f"{exc}\n视频导出暂不可用；其余功能不受影响。",
        ))
    except FFmpegCapabilityError as exc:
        issues.append(StartupIssue(
            "ffmpeg",
            f"{exc}\n视频导出暂不可用；其余功能不受影响。",
        ))
    except FFmpegError as exc:
        issues.append(StartupIssue("ffmpeg", str(exc)))

    config_dir = config_store.path.parent
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=config_dir, prefix=".avm_probe_",
                                         delete=False) as probe:
            probe_path = Path(probe.name)
        probe_path.unlink(missing_ok=True)
    except OSError:
        issues.append(StartupIssue(
            "config",
            f"配置目录不可写：{config_dir}\n"
            "设置与隐私确认将无法保存，请检查磁盘权限。",
        ))

    if issues:
        logger.warning("启动检查发现 %d 个问题", len(issues))
    else:
        logger.info("启动检查通过")
    return issues
