"""字幕业务：cue 划分、毫秒时间轴与 SRT 输出。

- 只生成 SRT 文件并返回相对路径；不直接保存或修改 Project
- cue 规则：≤32 字单 cue；长场景多条连续 cue；每条 ≤2 行、每行 ≤16 字；
  标点优先分段，无法分段时硬切；不丢字、不改字、不重排
- 时间轴：一律整数毫秒；场景内按字符数比例分配；
  场景末 cue 的 end_ms 强制等于场景结束；全项目末 cue 不超总时长
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from auto_video_maker.models.project import Project, Scene
from auto_video_maker.services.script_parser import (
    SECONDARY_ENDERS,
    SENTENCE_ENDERS,
    split_after_punctuation,
)

logger = logging.getLogger(__name__)

MAX_CUE_CHARS = 32
MAX_LINE_CHARS = 16
SUBTITLE_DIR_NAME = "subtitles"
SUBTITLE_FILE_NAME = "subtitles.srt"


class SubtitleServiceError(Exception):
    """字幕生成失败。消息面向用户。"""


@dataclass
class SubtitleCue:
    """一条字幕。"""

    index: int
    start_ms: int
    end_ms: int
    lines: list[str]


def _format_timestamp(ms: int) -> str:
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _chunk_scene_text(text: str) -> list[str]:
    """将场景文字拆成 ≤32 字的连续片段（标点优先，硬切兜底）。"""
    normalized = " ".join(text.split())
    if not normalized:
        return []
    if len(normalized) <= MAX_CUE_CHARS:
        return [normalized]
    # 句末标点 → 次级标点 → 硬切
    pieces: list[str] = []
    for sentence in split_after_punctuation(normalized, SENTENCE_ENDERS):
        if len(sentence) <= MAX_CUE_CHARS:
            pieces.append(sentence)
            continue
        for part in split_after_punctuation(sentence, SECONDARY_ENDERS):
            if len(part) <= MAX_CUE_CHARS:
                pieces.append(part)
            else:
                pieces.extend(
                    part[i:i + MAX_CUE_CHARS]
                    for i in range(0, len(part), MAX_CUE_CHARS)
                )
    # 贪心合并相邻小片段，尽量接近但不超过 32 字
    merged: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > MAX_CUE_CHARS:
            merged.append(current)
            current = piece
        else:
            current += piece
    if current:
        merged.append(current)
    return merged


def _wrap_lines(chunk: str) -> list[str]:
    """片段（≤32 字）换行为最多 2 行、每行 ≤16 字。"""
    stripped = chunk.strip()
    if len(stripped) <= MAX_LINE_CHARS:
        return [stripped]
    return [stripped[:MAX_LINE_CHARS], stripped[MAX_LINE_CHARS:]]


class SubtitleService:
    """根据场景时长生成 SRT 字幕文件。"""

    def build_cues(self, scenes: list[Scene]) -> list[SubtitleCue]:
        """计算全部字幕 cue（整数毫秒时间轴）。"""
        if not scenes:
            raise SubtitleServiceError("项目中没有场景，无法生成字幕。")
        for scene in scenes:
            if scene.duration is None or not scene.duration > 0:
                raise SubtitleServiceError(
                    f"第 {scene.scene_id} 个场景还没有配音。"
                    "请先为所有场景生成语音。"
                )
        cues: list[SubtitleCue] = []
        offset_ms = 0
        cue_index = 1
        for scene in scenes:
            scene_ms = round(scene.duration * 1000)
            scene_end_ms = offset_ms + scene_ms
            chunks = _chunk_scene_text(scene.text)
            if not chunks:
                offset_ms = scene_end_ms
                continue
            total_chars = sum(len(chunk) for chunk in chunks)
            start_ms = offset_ms
            for position, chunk in enumerate(chunks):
                if position == len(chunks) - 1:
                    end_ms = scene_end_ms  # 场景末 cue 对齐场景结束
                else:
                    share = scene_ms * len(chunk) // total_chars
                    end_ms = start_ms + max(share, 1)
                    end_ms = min(end_ms, scene_end_ms - 1)
                if end_ms <= start_ms:
                    end_ms = min(start_ms + 1, scene_end_ms)
                cues.append(
                    SubtitleCue(
                        index=cue_index,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        lines=_wrap_lines(chunk),
                    )
                )
                cue_index += 1
                start_ms = end_ms
            offset_ms = scene_end_ms
        if not cues:
            raise SubtitleServiceError("场景中没有可生成字幕的文字。")
        return cues

    def generate(self, project: Project, project_root: Path) -> str:
        """生成 SRT 文件，返回相对路径。不修改 Project。"""
        cues = self.build_cues(project.scenes)
        subtitle_dir = Path(project_root) / SUBTITLE_DIR_NAME
        subtitle_dir.mkdir(parents=True, exist_ok=True)
        target = subtitle_dir / SUBTITLE_FILE_NAME
        blocks = [
            (
                f"{cue.index}\n"
                f"{_format_timestamp(cue.start_ms)} --> {_format_timestamp(cue.end_ms)}\n"
                + "\n".join(cue.lines)
            )
            for cue in cues
        ]
        target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        logger.info("字幕已生成：%d 条 cue", len(cues))
        return f"{SUBTITLE_DIR_NAME}/{SUBTITLE_FILE_NAME}"
