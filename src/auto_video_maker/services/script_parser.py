"""文案处理低层级纯函数。

本模块只提供工具函数，不包含完整拆分策略。
完整的段落拆分、短句合并、长句再切策略在 scene_splitter.py 中。
"""

from __future__ import annotations

# 句末标点（见 TASK.md：。！？!?…）
SENTENCE_ENDERS = "。！？!?…"

# 次级标点（长句再切用）
SECONDARY_ENDERS = "，；、：,;"

# 句末标点之后可能紧跟的引号、括号收尾符（归前句）
CLOSING_MARKS = "”’」』）)】》〉>”’\""


def clean_script(text: str) -> str:
    """清理文案：统一换行、去除行首尾空白、压缩行内空白与空行。

    - 换行统一为 LF
    - 每行去除首尾空白（含全角空格），行内连续空白压为单个空格
    - 连续空行压缩为一个空行（段落分隔）
    - 去除首尾空行
    - 不修改任何有效文字
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw_line in normalized.split("\n"):
        line = " ".join(raw_line.split())
        lines.append(line)

    cleaned_lines: list[str] = []
    previous_blank = True  # 去除开头空行
    for line in lines:
        if line:
            cleaned_lines.append(line)
            previous_blank = False
        else:
            if not previous_blank:
                cleaned_lines.append("")
            previous_blank = True
    while cleaned_lines and cleaned_lines[-1] == "":
        cleaned_lines.pop()
    return "\n".join(cleaned_lines)


def normalize_for_comparison(text: str) -> str:
    """去除全部空白字符，用于「无丢字、无重复、顺序不变」不变量比较。"""
    return "".join(text.split())


def visible_length(text: str) -> int:
    """返回去除空白后的字符数（场景字数口径）。"""
    return len(normalize_for_comparison(text))


def split_into_paragraphs(cleaned_text: str) -> list[str]:
    """按空行拆分段落，返回非空段落列表。"""
    paragraphs = [part.strip() for part in cleaned_text.split("\n\n")]
    return [part for part in paragraphs if part]


def split_after_punctuation(text: str, enders: str) -> list[str]:
    """在指定标点之后切分文本，标点（及其后紧跟的收尾符）归前段。

    保留每一个字符：所有片段拼接后与输入完全一致。
    连续标点（如 ……、！！）作为一组处理，不在中间切开。
    """
    if not text:
        return []
    segments: list[str] = []
    buffer: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        buffer.append(char)
        if char in enders:
            # 吞并后续连续的同类标点与收尾符
            j = i + 1
            while j < length and (text[j] in enders or text[j] in CLOSING_MARKS):
                buffer.append(text[j])
                j += 1
            segments.append("".join(buffer))
            buffer = []
            i = j
        else:
            i += 1
    if buffer:
        segments.append("".join(buffer))
    return segments
