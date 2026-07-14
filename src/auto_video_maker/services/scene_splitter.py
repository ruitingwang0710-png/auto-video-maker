"""场景拆分：SceneSplitter 抽象接口与规则式默认实现。

接口约定（见 TASK.md）：
- 输入：清理后的文案字符串
- 输出：list[str] 场景文字列表
- 拆分器不负责创建 Scene；Scene 统一由 SceneService 创建
- 未来的 LLMSceneSplitter（Phase 2.5，置于 providers/）实现同一接口
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from auto_video_maker.services.script_parser import (
    SECONDARY_ENDERS,
    SENTENCE_ENDERS,
    split_after_punctuation,
    split_into_paragraphs,
    visible_length,
)

DEFAULT_MIN_SCENE_LENGTH = 15
DEFAULT_MAX_SCENE_LENGTH = 60


class SceneSplitter(ABC):
    """场景拆分器统一接口。"""

    @abstractmethod
    def split(self, cleaned_script: str) -> list[str]:
        """将清理后的文案拆分为场景文字列表。

        实现必须保证：无丢字、无重复、不改变原文顺序。
        """


class RuleBasedSceneSplitter(SceneSplitter):
    """规则式拆分器：默认基础能力与失败兜底，离线可运行。

    规则（见 TASK.md）：
    1. 优先按段落（空行）拆分；段落不超过最大长度时保留为一个场景
    2. 段落过长时按句末标点切句，标点归前句
    3. 超过最大长度的单句按次级标点再切；完全无标点时按最大长度硬切
    4. 短句合并：优先向后，无后句则向前，合并后不得超过最大长度；
       前后都无法合并时保留短场景
    5. 15–60 字为软目标；无丢字、无重复、顺序不变为硬约束
    """

    def __init__(
        self,
        min_length: int = DEFAULT_MIN_SCENE_LENGTH,
        max_length: int = DEFAULT_MAX_SCENE_LENGTH,
    ) -> None:
        if min_length <= 0 or max_length <= min_length:
            raise ValueError("场景长度阈值无效：要求 0 < min_length < max_length。")
        self._min_length = min_length
        self._max_length = max_length

    def split(self, cleaned_script: str) -> list[str]:
        scenes: list[str] = []
        for paragraph in split_into_paragraphs(cleaned_script):
            scenes.extend(self._split_paragraph(paragraph))
        return scenes

    # ------------------------------------------------------------ 内部策略

    def _split_paragraph(self, paragraph: str) -> list[str]:
        if visible_length(paragraph) <= self._max_length:
            return [paragraph]
        sentences = split_after_punctuation(paragraph, SENTENCE_ENDERS)
        pieces: list[str] = []
        for sentence in sentences:
            if visible_length(sentence) > self._max_length:
                pieces.extend(self._resplit_long_sentence(sentence))
            else:
                pieces.append(sentence)
        return self._merge_short_pieces(pieces)

    def _resplit_long_sentence(self, sentence: str) -> list[str]:
        """超长单句：按次级标点切块并贪心组合；无标点时硬切。"""
        chunks = split_after_punctuation(sentence, SECONDARY_ENDERS)
        # 单块仍超长（块内无任何次级标点）时硬切
        sized_chunks: list[str] = []
        for chunk in chunks:
            if visible_length(chunk) > self._max_length:
                sized_chunks.extend(self._hard_split(chunk))
            else:
                sized_chunks.append(chunk)
        # 贪心组合：尽量接近但不超过最大长度
        combined: list[str] = []
        current = ""
        for chunk in sized_chunks:
            if current and visible_length(current) + visible_length(chunk) > self._max_length:
                combined.append(current)
                current = chunk
            else:
                current += chunk
        if current:
            combined.append(current)
        return combined

    def _hard_split(self, text: str) -> list[str]:
        """按最大可见字数硬切，保留所有字符。"""
        pieces: list[str] = []
        buffer: list[str] = []
        count = 0
        for char in text:
            buffer.append(char)
            if not char.isspace():
                count += 1
            if count >= self._max_length:
                pieces.append("".join(buffer))
                buffer = []
                count = 0
        if buffer:
            pieces.append("".join(buffer))
        return pieces

    def _merge_short_pieces(self, pieces: list[str]) -> list[str]:
        """短句合并：优先向后合并，末句尝试向前合并，无法合并则保留。"""
        result: list[str] = []
        i = 0
        while i < len(pieces):
            current = pieces[i]
            # 向后合并：当前过短且与下一句合并后不超过上限
            while (
                visible_length(current) < self._min_length
                and i + 1 < len(pieces)
                and visible_length(current) + visible_length(pieces[i + 1]) <= self._max_length
            ):
                i += 1
                current += pieces[i]
            # 向前合并：仍过短且没有可用的后句时，尝试并入前一个结果
            if (
                visible_length(current) < self._min_length
                and result
                and visible_length(result[-1]) + visible_length(current) <= self._max_length
            ):
                result[-1] += current
            else:
                result.append(current)
            i += 1
        return result
