"""音频时长探测：mutagen 读取 mp3 实际时长。"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioProbeError(Exception):
    """无法读取音频时长。消息面向用户。"""


class AudioProbe:
    """基于 mutagen 的 mp3 时长读取（MP3.info.length，单位秒）。"""

    def duration_seconds(self, path: Path) -> float:
        file_path = Path(path)
        if not file_path.is_file():
            raise AudioProbeError(f"找不到音频文件：{file_path.name}")
        try:
            from mutagen.mp3 import MP3

            audio = MP3(file_path)
            length = float(audio.info.length)
        except AudioProbeError:
            raise
        except Exception as exc:  # noqa: BLE001 mutagen 的各类解析错误
            raise AudioProbeError(
                f"音频文件无法读取或已损坏：{file_path.name}"
            ) from exc
        if not length > 0:
            raise AudioProbeError(f"音频时长无效：{file_path.name}")
        return length
