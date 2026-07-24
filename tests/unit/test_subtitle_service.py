"""SubtitleService 测试：cue 划分、毫秒时间轴、SRT 格式。"""

import copy
import re
from pathlib import Path

import pytest

from auto_video_maker.models.project import Project, ProjectSettings, Scene
from auto_video_maker.services.script_parser import normalize_for_comparison
from auto_video_maker.services.subtitle_service import (
    SubtitleService,
    SubtitleServiceError,
    _chunk_scene_text,
    _format_timestamp,
    _wrap_lines,
)


def make_project(scenes: list[Scene]) -> Project:
    return Project(
        project_name="字幕测试",
        original_script="脚本",
        settings=ProjectSettings(output_directory="/tmp/out"),
        scenes=scenes,
    )


def scene(sid: int, text: str, duration: float | None) -> Scene:
    return Scene(scene_id=sid, text=text, duration=duration,
                 audio_path=f"audio/tts_{sid}.mp3" if duration else None)


class TestChunking:
    def test_short_scene_single_cue(self) -> None:
        assert _chunk_scene_text("这是一个不超过三十二个字符的场景。") == [
            "这是一个不超过三十二个字符的场景。"
        ]

    def test_long_scene_multiple_chunks(self) -> None:
        text = "第一句话讲了一个重要的事情。第二句话继续补充了很多细节内容。第三句话做了总结。"
        chunks = _chunk_scene_text(text)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 32
        # 不丢字、不改字、不重排
        assert normalize_for_comparison("".join(chunks)) == normalize_for_comparison(text)

    def test_punctuation_preferred(self) -> None:
        text = "前半句比较长包含了很多字，后半句也很长同样很多字。这里是第二句完整的话啊。"
        chunks = _chunk_scene_text(text)
        # 优先在标点后断开：每个片段应以标点结尾（本例可标点分段）
        assert all(chunk[-1] in "。，！？；、：" for chunk in chunks)

    def test_no_punctuation_hard_cut(self) -> None:
        text = "字" * 70
        chunks = _chunk_scene_text(text)
        assert all(len(chunk) <= 32 for chunk in chunks)
        assert "".join(chunks) == text

    def test_wrap_lines(self) -> None:
        assert _wrap_lines("短行") == ["短行"]
        long_chunk = "字" * 30
        lines = _wrap_lines(long_chunk)
        assert len(lines) == 2
        assert all(len(line) <= 16 for line in lines)
        assert "".join(lines) == long_chunk


class TestTimestamp:
    def test_format(self) -> None:
        assert _format_timestamp(0) == "00:00:00,000"
        assert _format_timestamp(1_234) == "00:00:01,234"
        assert _format_timestamp(3_661_007) == "01:01:01,007"


class TestBuildCues:
    def test_single_short_scene(self) -> None:
        cues = SubtitleService().build_cues([scene(1, "简短场景文字。", 2.0)])
        assert len(cues) == 1
        assert cues[0].start_ms == 0
        assert cues[0].end_ms == 2000

    def test_long_scene_multi_cue_end_aligned(self) -> None:
        """>32 字：多条连续 cue；场景末 cue end 等于场景结束。"""
        text = "第一句话讲了一个重要的事情。第二句话继续补充了很多细节内容。第三句话做了总结。"
        cues = SubtitleService().build_cues([scene(1, text, 10.0)])
        assert len(cues) >= 2
        assert cues[0].start_ms == 0
        assert cues[-1].end_ms == 10_000  # 舍入误差被末 cue 吸收
        # 连续无重叠、start<end
        for i, cue in enumerate(cues):
            assert cue.start_ms < cue.end_ms
            if i > 0:
                assert cue.start_ms == cues[i - 1].end_ms
        # 文字完整
        joined = "".join("".join(cue.lines) for cue in cues)
        assert normalize_for_comparison(joined) == normalize_for_comparison(text)

    def test_proportional_allocation(self) -> None:
        # 两个等长片段应大致均分时长
        text = "字" * 64  # 硬切为 32+32
        cues = SubtitleService().build_cues([scene(1, text, 8.0)])
        assert len(cues) == 2
        first_share = cues[0].end_ms - cues[0].start_ms
        assert 3_500 <= first_share <= 4_500

    def test_multi_scene_cumulative_no_overlap(self) -> None:
        scenes = [
            scene(1, "第一个场景的文字。", 1.5),
            scene(2, "第二个场景的文字。", 2.25),
            scene(3, "第三个场景的文字。", 0.75),
        ]
        cues = SubtitleService().build_cues(scenes)
        assert cues[0].start_ms == 0
        assert cues[-1].end_ms == round((1.5 + 2.25 + 0.75) * 1000)  # 不超总时长
        for i in range(1, len(cues)):
            assert cues[i].start_ms >= cues[i - 1].end_ms
        assert [cue.index for cue in cues] == list(range(1, len(cues) + 1))

    def test_missing_duration_rejected(self) -> None:
        with pytest.raises(SubtitleServiceError, match="先为所有场景生成语音"):
            SubtitleService().build_cues(
                [scene(1, "有配音。", 2.0), scene(2, "没配音。", None)]
            )

    def test_empty_scenes_rejected(self) -> None:
        with pytest.raises(SubtitleServiceError):
            SubtitleService().build_cues([])


class TestGenerate:
    def test_srt_file_format(self, tmp_path: Path) -> None:
        project = make_project([
            scene(1, "第一个场景的中文字幕内容。", 2.0),
            scene(2, "第二个场景的中文字幕内容。", 3.0),
        ])
        relative = SubtitleService().generate(project, tmp_path)
        assert relative == "subtitles/subtitles.srt"
        content = (tmp_path / relative).read_text(encoding="utf-8")
        blocks = content.strip().split("\n\n")
        assert len(blocks) == 2
        # 序号、时间行格式
        first = blocks[0].split("\n")
        assert first[0] == "1"
        assert re.match(
            r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$", first[1]
        )
        assert "第一个场景的中文字幕内容。" in blocks[0]

    def test_generate_does_not_modify_project(self, tmp_path: Path) -> None:
        """专项：SubtitleService 不直接修改 Project。"""
        project = make_project([scene(1, "场景文字。", 2.0)])
        snapshot = copy.deepcopy(project.to_dict())
        SubtitleService().generate(project, tmp_path)
        assert project.to_dict() == snapshot  # 包括 output.subtitle_path 未被写

    def test_regenerate_overwrites(self, tmp_path: Path) -> None:
        project = make_project([scene(1, "旧文字内容。", 2.0)])
        SubtitleService().generate(project, tmp_path)
        project.scenes[0].text = "新文字内容。"
        SubtitleService().generate(project, tmp_path)
        content = (tmp_path / "subtitles/subtitles.srt").read_text(encoding="utf-8")
        assert "新文字内容" in content
        assert "旧文字内容" not in content
