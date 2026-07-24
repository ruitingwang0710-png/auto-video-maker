"""FFmpeg 封装：定位、能力预检、执行、进度、进程组终止与路径安全。

- 唯一允许构造/执行 FFmpeg 命令与转义路径的模块（ARCHITECTURE 4.8）
- 绝不使用 shell=True（既定规则）
- FFmpeg 作为独立进程组启动；取消时 terminate 进程组，3 秒后 kill
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from auto_video_maker.infrastructure.config import ConfigStore

logger = logging.getLogger(__name__)

REQUIRED_ENCODERS = ("libx264", "aac")
REQUIRED_FILTERS = ("zoompan", "boxblur", "subtitles", "scale", "overlay")
_KILL_GRACE_SECONDS = 3.0
_STDERR_TAIL_LINES = 40


class FFmpegError(Exception):
    """FFmpeg 相关错误。消息面向用户。"""


class FFmpegNotFoundError(FFmpegError):
    """未找到 ffmpeg/ffprobe。"""


class FFmpegCapabilityError(FFmpegError):
    """FFmpeg 存在但缺少必需能力。"""

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        super().__init__(
            "当前 FFmpeg 缺少必需能力："
            + "、".join(missing)
            + "。请安装完整版 FFmpeg（macOS: brew install ffmpeg）。"
        )


class FFmpegExecutionError(FFmpegError):
    """FFmpeg 执行失败（含 stderr 摘要）。"""

    def __init__(self, message: str, stderr_tail: str = "") -> None:
        self.stderr_tail = stderr_tail
        detail = f"\n\nFFmpeg 输出（末尾）：\n{stderr_tail}" if stderr_tail else ""
        super().__init__(message + detail)


class FFmpegCancelledError(FFmpegError):
    """任务被用户取消。"""


class CancelToken:
    """跨线程取消标记。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class FFmpegCapabilities:
    ffmpeg_path: Path
    ffprobe_path: Path
    version: str
    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing


# ------------------------------------------------------------ 路径安全（唯一实现）


def escape_filter_value(value: str) -> str:
    """转义 filtergraph 中的文件路径/文本值（单引号包裹形式）。

    处理中文、空格、单引号、冒号、反斜杠。
    """
    # filtergraph 引号规则：单引号内不能再含单引号，需 '\'' 形式拼接
    escaped = value.replace("\\", "\\\\").replace("'", r"'\''")
    return f"'{escaped}'"


def concat_list_line(path: Path) -> str:
    """生成 concat demuxer 列表中的一行（安全转义单引号）。"""
    text = str(path).replace("'", r"'\''")
    return f"file '{text}'"


class FFmpegRunner:
    """FFmpeg / ffprobe 的定位、预检与执行封装。"""

    def __init__(
        self,
        config_store: ConfigStore | None = None,
        app_bin_dir: Path | None = None,
    ) -> None:
        self._config_store = config_store
        self._app_bin_dir = app_bin_dir
        self._located: tuple[Path, Path] | None = None
        self._capabilities: FFmpegCapabilities | None = None

    # ------------------------------------------------------------ 定位

    def locate(self) -> tuple[Path, Path]:
        """按三级顺序定位 (ffmpeg, ffprobe)。找不到抛 FFmpegNotFoundError。"""
        if self._located is not None:
            return self._located
        ffmpeg = self._locate_ffmpeg()
        ffprobe = self._locate_ffprobe(ffmpeg)
        self._located = (ffmpeg, ffprobe)
        logger.info("FFmpeg 定位: %s / %s", ffmpeg, ffprobe)
        return self._located

    def _configured(self, key: str) -> str:
        if self._config_store is None:
            return ""
        return getattr(self._config_store.load(), key, "") or ""

    def _locate_ffmpeg(self) -> Path:
        configured = self._configured("ffmpeg_path").strip()
        if configured:
            path = Path(configured).expanduser()
            if path.is_file():
                return path
            raise FFmpegNotFoundError(
                f"配置的 ffmpeg 路径不存在：{configured}"
            )
        if self._app_bin_dir is not None:
            bundled = self._app_bin_dir / "ffmpeg"
            if bundled.is_file():
                return bundled
        found = shutil.which("ffmpeg")
        if found:
            return Path(found)
        raise FFmpegNotFoundError(
            "未找到 FFmpeg。请先安装（macOS: brew install ffmpeg）。"
        )

    def _locate_ffprobe(self, ffmpeg: Path) -> Path:
        # 配置了 ffmpeg_path 时：先同目录，再独立配置，再 PATH
        if self._configured("ffmpeg_path").strip():
            sibling = ffmpeg.parent / "ffprobe"
            if sibling.is_file():
                return sibling
        configured = self._configured("ffprobe_path").strip()
        if configured:
            path = Path(configured).expanduser()
            if path.is_file():
                return path
        if self._app_bin_dir is not None:
            bundled = self._app_bin_dir / "ffprobe"
            if bundled.is_file():
                return bundled
        sibling = ffmpeg.parent / "ffprobe"
        if sibling.is_file():
            return sibling
        found = shutil.which("ffprobe")
        if found:
            return Path(found)
        raise FFmpegNotFoundError(
            "未找到 ffprobe。请安装完整版 FFmpeg（macOS: brew install ffmpeg）。"
        )

    # ------------------------------------------------------------ 能力预检

    def check_capabilities(self, require_subtitles: bool = True) -> FFmpegCapabilities:
        """验证版本、编码器与滤镜能力；缺失抛 FFmpegCapabilityError。"""
        if self._capabilities is None:
            ffmpeg, ffprobe = self.locate()
            version = self._capture([str(ffmpeg), "-version"]).splitlines()[0]
            self._capture([str(ffprobe), "-version"])
            encoders = self._capture([str(ffmpeg), "-hide_banner", "-encoders"])
            filters = self._capture([str(ffmpeg), "-hide_banner", "-filters"])
            missing: list[str] = []
            for encoder in REQUIRED_ENCODERS:
                if f" {encoder} " not in encoders:
                    missing.append(f"编码器 {encoder}")
            for filter_name in REQUIRED_FILTERS:
                if f" {filter_name} " not in filters:
                    missing.append(f"滤镜 {filter_name}")
            self._capabilities = FFmpegCapabilities(
                ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
                version=version, missing=missing,
            )
        caps = self._capabilities
        relevant = caps.missing if require_subtitles else [
            item for item in caps.missing if "subtitles" not in item
        ]
        if relevant:
            raise FFmpegCapabilityError(relevant)
        return caps

    def _capture(self, cmd: list[str]) -> str:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=20, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FFmpegNotFoundError(f"无法执行 {cmd[0]}。") from exc
        if result.returncode != 0:
            raise FFmpegNotFoundError(f"{Path(cmd[0]).name} 无法正常运行。")
        return result.stdout + result.stderr

    # ------------------------------------------------------------ 执行

    def run(
        self,
        args: list[str],
        expected_duration_ms: int | None = None,
        on_progress: Callable[[float], None] | None = None,
        cancel_token: CancelToken | None = None,
        step_name: str = "FFmpeg",
    ) -> None:
        """执行一次 ffmpeg 命令（参数列表，绝不 shell=True）。

        - -progress 管道解析实时进度（0.0–1.0 回调）
        - cancel_token 触发时 terminate 进程组，3 秒后 kill
        - 失败抛 FFmpegExecutionError（含 stderr 末尾摘要）
        """
        ffmpeg, _ = self.locate()
        cmd = [str(ffmpeg), "-hide_banner", "-y",
               "-progress", "pipe:1", "-nostats", *args]
        logger.info("%s 开始（%d 个参数）", step_name, len(cmd))
        stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # 独立进程组
        )

        def _drain_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_tail.append(line.rstrip())

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        cancelled = False
        assert process.stdout is not None
        try:
            for line in process.stdout:
                if cancel_token is not None and cancel_token.cancelled:
                    cancelled = True
                    self._terminate_group(process)
                    break
                if on_progress and expected_duration_ms:
                    key, _, value = line.strip().partition("=")
                    if key == "out_time_us" and value.isdigit():
                        ratio = int(value) / 1000 / expected_duration_ms
                        on_progress(min(1.0, max(0.0, ratio)))
            process.wait()
        finally:
            stderr_thread.join(timeout=2)
            if process.poll() is None:
                self._terminate_group(process)
                process.wait()
        if cancelled or (cancel_token is not None and cancel_token.cancelled):
            raise FFmpegCancelledError("导出已取消。")
        if process.returncode != 0:
            raise FFmpegExecutionError(
                f"{step_name} 执行失败。", "\n".join(stderr_tail)
            )
        if on_progress and expected_duration_ms:
            on_progress(1.0)

    def _terminate_group(self, process: subprocess.Popen) -> None:
        """terminate 整个进程组；宽限期后 kill。"""
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + _KILL_GRACE_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.05)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    # ------------------------------------------------------------ 探测

    def probe(self, path: Path) -> dict:
        """ffprobe JSON（streams + format）。失败抛 FFmpegExecutionError。"""
        _, ffprobe = self.locate()
        try:
            result = subprocess.run(
                [str(ffprobe), "-v", "error", "-print_format", "json",
                 "-show_streams", "-show_format", str(path)],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FFmpegExecutionError("无法执行 ffprobe。") from exc
        if result.returncode != 0:
            raise FFmpegExecutionError(
                f"无法读取媒体文件：{Path(path).name}",
                result.stderr.strip()[-500:],
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FFmpegExecutionError("ffprobe 输出无法解析。") from exc

    # ------------------------------------------------------------ 文件生成助手

    def write_concat_list(self, clip_paths: list[Path], target: Path) -> Path:
        """安全生成 concat demuxer 列表文件（统一转义实现）。"""
        lines = [concat_list_line(path) for path in clip_paths]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    def write_filter_script(self, filtergraph: str, target: Path) -> Path:
        """将复杂滤镜写入 filter_complex_script 文件。"""
        target.write_text(filtergraph, encoding="utf-8")
        return target

    def build_subtitles_filter(self, srt_path: Path, force_style: str) -> str:
        """构造 subtitles 滤镜串（路径经统一转义）。"""
        return (
            f"subtitles=filename={escape_filter_value(str(srt_path))}"
            f":force_style={escape_filter_value(force_style)}"
        )
