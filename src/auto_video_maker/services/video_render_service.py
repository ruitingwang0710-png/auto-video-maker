"""视频渲染编排：单次编码片段 + concat stream copy + staging 事务。

关键规则（TASK.md Phase 5）：
- 音频是场景时长的权威来源；frame_count = ceil(duration_ms × fps / 1000)
- 每场景片段一次编码完成：图片动效 + 配音 + 局部字幕烧录
- concat 用 stream copy，最终仅 faststart remux
- 片段缓存键用图片/音频内容 SHA-256；命中经 ffprobe 六项验证
- 临时 MP4 一律 *.part.mp4 命名
- staging 事务：全部验证成功才原子替换 output/ 三件套；
  失败/取消保留上一次成功输出
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import uuid
from pathlib import Path
from typing import Callable

from auto_video_maker.infrastructure.ffmpeg_runner import (
    CancelToken,
    FFmpegCancelledError,
    FFmpegError,
    FFmpegRunner,
)
from auto_video_maker.models.project import Project, Scene
from auto_video_maker.services.credits_service import CreditsService
from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService, SceneServiceError
from auto_video_maker.services.subtitle_service import (
    SubtitleCue,
    SubtitleService,
    _format_timestamp,
)

logger = logging.getLogger(__name__)

RENDERER_SCHEMA_VERSION = 2  # v2: 末端 full→limited range 转换（FFmpeg 8 兼容）
FPS = 30
CLIP_TOLERANCE_MS = 50
TOTAL_TOLERANCE_MS = 200
SUBTITLE_STYLE = "FontName=PingFang SC,FontSize=14,Outline=2,MarginV=80"
CLIPS_DIR = "temp/clips"
OUTPUT_DIR = "output"

ENCODE_PARAMS: dict = {
    "vcodec": "libx264",
    "preset": "medium",
    "crf": "20",
    "pix_fmt": "yuv420p",
    "acodec": "aac",
    "audio_bitrate": "192k",
    "audio_rate": "44100",
    "fps": FPS,
}
EFFECT_PARAMS: dict = {"kenburns": "center-zoom", "from": "1.00", "to": "1.08"}


class VideoRenderError(Exception):
    """导出失败。消息面向用户。"""


class ExportValidationError(VideoRenderError):
    """素材不完整，导出被拒绝。"""


class ProjectSaveAfterRenderError(VideoRenderError):
    """视频已生成，但 project.json 保存失败（视频保留）。"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_count_for(duration_ms: int, fps: int = FPS) -> int:
    """帧数向上取整：视频轨必须覆盖完整音频，绝不短于音频。"""
    return max(1, math.ceil(duration_ms * fps / 1000))


class VideoRenderService:
    """导出编排（不含 UI；命令细节经 FFmpegRunner）。"""

    def __init__(
        self,
        ffmpeg: FFmpegRunner,
        subtitle_service: SubtitleService,
        credits_service: CreditsService,
        project_manager: ProjectManager,
        scene_service: SceneService,
    ) -> None:
        self._ffmpeg = ffmpeg
        self._subtitles = subtitle_service
        self._credits = credits_service
        self._project_manager = project_manager
        self._scene_service = scene_service

    # ------------------------------------------------------------ 校验

    def validate_export(self, project: Project) -> list[str]:
        """返回阻碍导出的问题列表（空列表 = 可导出）。"""
        issues: list[str] = []
        try:
            self._ffmpeg.check_capabilities(
                require_subtitles=project.settings.subtitle_enabled
            )
        except FFmpegError as exc:
            issues.append(str(exc))
        if not project.scenes:
            issues.append("项目中没有场景。")
            return issues
        root = self._project_manager.project_directory(project)
        for scene in project.scenes:
            asset = scene.selected_asset or {}
            local_path = asset.get("local_path") or ""
            if not local_path or not (root / local_path).is_file():
                issues.append(f"场景 {scene.scene_id}：缺少图片")
            if (
                not scene.audio_path
                or not scene.duration
                or not (root / scene.audio_path).is_file()
            ):
                issues.append(f"场景 {scene.scene_id}：缺少配音")
        return issues

    # ------------------------------------------------------------ 缓存键

    def clip_cache_key(
        self,
        image_path: Path,
        audio_path: Path,
        duration_ms: int,
        resolution: str,
        subtitle_enabled: bool,
        local_cues: list[SubtitleCue],
    ) -> str:
        payload = json.dumps(
            {
                "renderer_schema_version": RENDERER_SCHEMA_VERSION,
                "image_sha256": _sha256_file(image_path),
                "audio_sha256": _sha256_file(audio_path),
                "duration_ms": duration_ms,
                "resolution": resolution,
                "fps": FPS,
                "effect_params": EFFECT_PARAMS,
                "encode_params": ENCODE_PARAMS,
                "subtitle_enabled": subtitle_enabled,
                "subtitle_style": SUBTITLE_STYLE if subtitle_enabled else "",
                "subtitle_cues": [
                    [cue.start_ms, cue.end_ms, "\n".join(cue.lines)]
                    for cue in local_cues
                ] if subtitle_enabled else [],
            },
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    # ------------------------------------------------------------ 渲染

    def render(
        self,
        project: Project,
        on_progress: Callable[[int], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> str:
        """执行完整导出事务，返回相对路径 output/final_video.mp4。"""
        report = on_progress or (lambda pct: None)
        issues = self.validate_export(project)
        if issues:
            raise ExportValidationError("无法导出：\n" + "\n".join(issues))
        root = self._project_manager.project_directory(project)
        width, height = self._resolution(project)
        report(1)

        # 标准 SRT 重新生成（不依赖旧引用）
        srt_relative = self._subtitles.generate(project, root)
        self._project_manager.set_subtitle_path(project, srt_relative)
        report(3)

        staging = root / "temp" / f"export_{uuid.uuid4().hex[:8]}"
        staging.mkdir(parents=True, exist_ok=True)
        try:
            clip_paths = self._render_clips(
                project, root, staging, width, height, cancel_token,
                lambda pct: report(3 + int(pct * 0.82)),  # 3–85
            )
            self._check_cancel(cancel_token)

            concat_out = staging / "concat.part.mp4"
            concat_list = self._ffmpeg.write_concat_list(
                clip_paths, staging / "concat_list.txt"
            )
            total_ms = sum(round(s.duration * 1000) for s in project.scenes)
            self._ffmpeg.run(
                ["-f", "concat", "-safe", "0", "-i", str(concat_list),
                 "-c", "copy", "-f", "mp4", str(concat_out)],
                expected_duration_ms=total_ms,
                cancel_token=cancel_token,
                step_name="合并场景",
            )
            report(90)

            final_part = staging / "final_video.part.mp4"
            self._ffmpeg.run(
                ["-i", str(concat_out), "-c", "copy",
                 "-movflags", "+faststart", "-f", "mp4", str(final_part)],
                expected_duration_ms=total_ms,
                cancel_token=cancel_token,
                step_name="写入输出",
            )
            report(94)

            self._verify_final(final_part, width, height, total_ms)

            srt_part = staging / "subtitles.part.srt"
            shutil.copyfile(root / srt_relative, srt_part)
            credits_part = staging / "credits.part.txt"
            self._credits.generate(project, credits_part)
            report(96)

            # 原子替换 output/ 三件套（全部就绪后才替换）
            output_dir = root / OUTPUT_DIR
            output_dir.mkdir(parents=True, exist_ok=True)
            final_part.replace(output_dir / "final_video.mp4")
            srt_part.replace(output_dir / "subtitles.srt")
            credits_part.replace(output_dir / "credits.txt")
            report(98)
        except FFmpegCancelledError:
            raise VideoRenderError("导出已取消。") from None
        finally:
            shutil.rmtree(staging, ignore_errors=True)  # staging 与半成品清理

        # 成功后写引用并保存；保存失败时视频保留并明确报错
        video_relative = f"{OUTPUT_DIR}/final_video.mp4"
        self._project_manager.set_video_path(project, video_relative)
        try:
            self._scene_service.save(project)
        except SceneServiceError as exc:
            raise ProjectSaveAfterRenderError(
                f"视频已生成（{video_relative}），但项目状态保存失败：{exc}"
            ) from exc
        report(100)
        logger.info("导出完成: %s", video_relative)
        return video_relative

    # ------------------------------------------------------------ 片段

    def _render_clips(
        self,
        project: Project,
        root: Path,
        staging: Path,
        width: int,
        height: int,
        cancel_token: CancelToken | None,
        report: Callable[[float], None],
    ) -> list[Path]:
        clips_dir = root / CLIPS_DIR
        clips_dir.mkdir(parents=True, exist_ok=True)
        total = len(project.scenes)
        clip_paths: list[Path] = []
        for position, scene in enumerate(project.scenes):
            self._check_cancel(cancel_token)
            base = position * 100.0 / total
            span = 100.0 / total
            clip = self._ensure_clip(
                project, scene, root, staging, clips_dir, width, height,
                cancel_token,
                lambda ratio, b=base, s=span: report(b + ratio * s),
            )
            clip_paths.append(clip)
            report((position + 1) * 100.0 / total)
        return clip_paths

    def _ensure_clip(
        self,
        project: Project,
        scene: Scene,
        root: Path,
        staging: Path,
        clips_dir: Path,
        width: int,
        height: int,
        cancel_token: CancelToken | None,
        report: Callable[[float], None],
    ) -> Path:
        image_path = root / scene.selected_asset["local_path"]
        audio_path = root / scene.audio_path
        duration_ms = round(scene.duration * 1000)
        subtitle_enabled = project.settings.subtitle_enabled
        local_cues = (
            self._subtitles.build_cues([scene]) if subtitle_enabled else []
        )
        key = self.clip_cache_key(
            image_path, audio_path, duration_ms,
            f"{width}x{height}", subtitle_enabled, local_cues,
        )
        cached = clips_dir / f"clip_{key}.mp4"
        if cached.is_file() and self._clip_valid(cached, width, height, duration_ms):
            logger.info("片段缓存命中: %s", cached.name)
            report(1.0)
            return cached

        frames = frame_count_for(duration_ms)
        # 局部 SRT（时间从 00:00:00,000 起）
        local_srt: Path | None = None
        if subtitle_enabled:
            local_srt = staging / f"scene_{scene.scene_id:03d}.srt"
            self._write_local_srt(local_cues, local_srt)

        filtergraph = self._build_clip_filtergraph(
            width, height, frames, local_srt
        )
        script = self._ffmpeg.write_filter_script(
            filtergraph, staging / f"filter_{scene.scene_id:03d}.txt"
        )
        part = clips_dir / f"clip_{key}.part.mp4"
        args = [
            "-i", str(image_path),
            "-i", str(audio_path),
            "-filter_complex_script", str(script),
            "-map", "[vout]", "-map", "1:a",
            "-c:v", ENCODE_PARAMS["vcodec"],
            "-preset", ENCODE_PARAMS["preset"],
            "-crf", ENCODE_PARAMS["crf"],
            "-pix_fmt", ENCODE_PARAMS["pix_fmt"],
            "-color_range", "tv",
            "-r", str(FPS),
            "-c:a", ENCODE_PARAMS["acodec"],
            "-b:a", ENCODE_PARAMS["audio_bitrate"],
            "-ar", ENCODE_PARAMS["audio_rate"],
            "-f", "mp4",
            str(part),
        ]
        try:
            self._ffmpeg.run(
                args,
                expected_duration_ms=duration_ms,
                on_progress=report,
                cancel_token=cancel_token,
                step_name=f"渲染场景 {scene.scene_id}",
            )
            info = self._ffmpeg.probe(part)
            failures, diagnostics = self._validate_media(
                info, width, height, duration_ms, CLIP_TOLERANCE_MS
            )
            if failures:
                logger.warning(
                    "场景片段验证未通过 file=%s 失败字段=%s 诊断=%s",
                    part.name, failures, diagnostics,
                )
                raise VideoRenderError(
                    f"场景 {scene.scene_id} 渲染结果未通过验证"
                    f"（失败字段：{'、'.join(failures)}）。"
                )
            part.replace(cached)
        finally:
            part.unlink(missing_ok=True)
        return cached

    def _build_clip_filtergraph(
        self, width: int, height: int, frames: int, local_srt: Path | None
    ) -> str:
        """片段滤镜链。

        顺序：图片适配（cover 模糊背景 + contain 前景）→ overlay →
        zoompan → subtitles（启用时）→ 末端 range/format 转换。

        末端转换（必须是最后一段）：JPEG 等 full-range 源显式转
        limited-range 再钉死 yuv420p——
        scale=in_range=pc:out_range=tv,format=yuv420p。
        FFmpeg 8 中 yuvj420p 已弃用为 yuv420p+full range，若转换位于
        subtitles 之前，后续协商会把 full-range 重新传播到编码器，
        导致 ffprobe 报 yuvj420p；因此有无字幕两条路径都以本段收尾。
        """
        zoom_span = max(1, frames - 1)
        chains = [
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=20:2[bg]",
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease[fg]",
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[comp]",
            f"[comp]scale={width * 2}:{height * 2},"
            f"zoompan=z='1+0.08*on/{zoom_span}':d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={width}x{height}:fps={FPS}[zoomed]",
        ]
        pre_final = "[zoomed]"
        if local_srt is not None:
            chains.append(
                "[zoomed]"
                + self._ffmpeg.build_subtitles_filter(local_srt, SUBTITLE_STYLE)
                + "[subbed]"
            )
            pre_final = "[subbed]"
        chains.append(
            f"{pre_final}scale=in_range=pc:out_range=tv,"
            f"format={ENCODE_PARAMS['pix_fmt']}[vout]"
        )
        return ";\n".join(chains)

    @staticmethod
    def _write_local_srt(cues: list[SubtitleCue], target: Path) -> None:
        blocks = [
            f"{cue.index}\n"
            f"{_format_timestamp(cue.start_ms)} --> {_format_timestamp(cue.end_ms)}\n"
            + "\n".join(cue.lines)
            for cue in cues
        ]
        target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")

    # ------------------------------------------------------------ 验证

    def _clip_valid(self, path: Path, width: int, height: int, duration_ms: int) -> bool:
        """缓存/片段验证：全部字段满足才算有效（失败时逐字段记录诊断）。"""
        try:
            info = self._ffmpeg.probe(path)
        except FFmpegError as exc:
            logger.warning("片段验证：ffprobe 失败 file=%s err=%s", Path(path).name, exc)
            return False
        failures, diagnostics = self._validate_media(
            info, width, height, duration_ms, CLIP_TOLERANCE_MS
        )
        if failures:
            logger.warning(
                "片段验证未通过 file=%s 失败字段=%s 诊断=%s",
                Path(path).name, failures, diagnostics,
            )
            return False
        return True

    def _verify_final(
        self, path: Path, width: int, height: int, total_ms: int
    ) -> None:
        info = self._ffmpeg.probe(path)
        failures, diagnostics = self._validate_media(
            info, width, height, total_ms, TOTAL_TOLERANCE_MS
        )
        if failures:
            logger.warning(
                "最终验证未通过 file=%s 失败字段=%s 诊断=%s",
                Path(path).name, failures, diagnostics,
            )
            raise VideoRenderError(
                "导出结果未通过验证（失败字段：" + "、".join(failures) + "）。"
            )

    # ------------------------------------------------------------ 逐字段验证

    @staticmethod
    def _parse_fraction(value: object) -> float | None:
        try:
            num, _, den = str(value).partition("/")
            return float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError, TypeError):
            return None

    @classmethod
    def _stream_duration_ms(cls, stream: dict | None) -> tuple[float | None, float | None]:
        """流时长（毫秒）：duration 字段 + duration_ts×time_base 两种来源。"""
        if stream is None:
            return None, None
        direct: float | None = None
        try:
            direct = float(stream["duration"]) * 1000
        except (KeyError, TypeError, ValueError):
            direct = None
        derived: float | None = None
        duration_ts = stream.get("duration_ts")
        time_base = cls._parse_fraction(stream.get("time_base"))
        if isinstance(duration_ts, int) and time_base:
            derived = duration_ts * time_base * 1000
        return direct, derived

    @classmethod
    def _validate_media(
        cls, info: dict, width: int, height: int,
        expected_ms: int, tolerance_ms: int,
    ) -> tuple[list[str], dict]:
        """逐字段验证；返回 (失败字段列表, 诊断字典)。

        诊断只含数值与枚举，不含用户文案或完整路径。
        标准保持严格：宽高/h264/yuv420p/双流必须精确；
        帧率取 avg_frame_rate 或 r_frame_rate 任一等于 30（±0.01）
        （r_frame_rate 是猜测的最大帧率，跨 FFmpeg 版本敏感；
        avg_frame_rate 为实测平均值）；
        时长优先 format.duration，缺失时按 视频流 duration →
        duration_ts×time_base → 音频流 duration 依次回退，容差不变。
        """
        streams = info.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        video_direct, video_derived = cls._stream_duration_ms(video)
        audio_direct, audio_derived = cls._stream_duration_ms(audio)
        try:
            format_ms: float | None = float(info["format"]["duration"]) * 1000
        except (KeyError, TypeError, ValueError):
            format_ms = None
        avg_fps = cls._parse_fraction((video or {}).get("avg_frame_rate"))
        r_fps = cls._parse_fraction((video or {}).get("r_frame_rate"))

        # 时长来源优先级（不因单个可选字段缺失而误判）
        actual_ms = next(
            (v for v in (format_ms, video_direct, video_derived, audio_direct,
                         audio_derived) if v is not None),
            None,
        )
        lower = expected_ms - tolerance_ms
        upper = expected_ms + tolerance_ms + 1000 / FPS * 2  # ceil 允许略长

        diagnostics = {
            "has_video_stream": video is not None,
            "has_audio_stream": audio is not None,
            "width": (video or {}).get("width"),
            "height": (video or {}).get("height"),
            "expected_size": f"{width}x{height}",
            "codec_name": (video or {}).get("codec_name"),
            "pix_fmt": (video or {}).get("pix_fmt"),
            "audio_codec": (audio or {}).get("codec_name"),
            "avg_frame_rate": (video or {}).get("avg_frame_rate"),
            "r_frame_rate": (video or {}).get("r_frame_rate"),
            "avg_fps": avg_fps,
            "r_fps": r_fps,
            "format_duration_ms": format_ms,
            "video_stream_duration_ms": video_direct,
            "video_duration_ts_ms": video_derived,
            "audio_stream_duration_ms": audio_direct,
            "audio_duration_ts_ms": audio_derived,
            "video_start_time": (video or {}).get("start_time"),
            "audio_start_time": (audio or {}).get("start_time"),
            "expected_duration_ms": expected_ms,
            "actual_duration_ms": actual_ms,
            "duration_delta_ms": (
                None if actual_ms is None else round(actual_ms - expected_ms, 1)
            ),
            "allowed_range_ms": f"[{lower}, {round(upper, 1)}]",
        }

        failures: list[str] = []
        if video is None:
            failures.append("has_video_stream")
        if audio is None:
            failures.append("has_audio_stream")
        if video is not None:
            if video.get("width") != width or video.get("height") != height:
                failures.append("width/height")
            if video.get("codec_name") != "h264":
                failures.append("codec_name")
            if video.get("pix_fmt") != ENCODE_PARAMS["pix_fmt"]:
                failures.append("pix_fmt")
            fps_ok = any(
                rate is not None and abs(rate - FPS) <= 0.01
                for rate in (avg_fps, r_fps)
            )
            if not fps_ok:
                failures.append("frame_rate")
        if audio is not None and audio.get("codec_name") != "aac":
            failures.append("audio_codec")
        if actual_ms is None:
            failures.append("duration_missing")
        elif not (lower <= actual_ms <= upper):
            failures.append("duration_out_of_range")
        return failures, diagnostics

    @staticmethod
    def _resolution(project: Project) -> tuple[int, int]:
        try:
            width_str, height_str = project.settings.resolution.split("x")
            return int(width_str), int(height_str)
        except ValueError as exc:
            raise VideoRenderError(
                f"分辨率设置无效：{project.settings.resolution}"
            ) from exc

    @staticmethod
    def _check_cancel(cancel_token: CancelToken | None) -> None:
        if cancel_token is not None and cancel_token.cancelled:
            raise VideoRenderError("导出已取消。")
