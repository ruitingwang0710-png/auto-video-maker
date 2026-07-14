"""script_parser 低层级纯函数测试。"""

from auto_video_maker.services.script_parser import (
    SECONDARY_ENDERS,
    SENTENCE_ENDERS,
    clean_script,
    normalize_for_comparison,
    split_after_punctuation,
    split_into_paragraphs,
    visible_length,
)


class TestCleanScript:
    def test_normalizes_line_endings(self) -> None:
        assert clean_script("第一行\r\n第二行\r第三行") == "第一行\n第二行\n第三行"

    def test_strips_fullwidth_and_ascii_whitespace(self) -> None:
        assert clean_script("　 你好 　") == "你好"

    def test_collapses_inner_whitespace(self) -> None:
        assert clean_script("你好   世界　　朋友") == "你好 世界 朋友"

    def test_collapses_blank_lines(self) -> None:
        assert clean_script("段一\n\n\n\n段二") == "段一\n\n段二"

    def test_strips_leading_trailing_blank_lines(self) -> None:
        assert clean_script("\n\n段一\n\n") == "段一"

    def test_keeps_all_chinese_content(self) -> None:
        raw = "  人工智能。\r\n\r\n\r\n  改变  世界。  "
        assert normalize_for_comparison(clean_script(raw)) == normalize_for_comparison(raw)

    def test_empty_input(self) -> None:
        assert clean_script("") == ""
        assert clean_script("   \n 　 \n ") == ""


class TestNormalizeForComparison:
    def test_removes_all_whitespace(self) -> None:
        assert normalize_for_comparison("你 好\n世\t界　！") == "你好世界！"

    def test_visible_length(self) -> None:
        assert visible_length("你 好 世 界") == 4
        assert visible_length("") == 0


class TestSplitIntoParagraphs:
    def test_splits_on_blank_lines(self) -> None:
        assert split_into_paragraphs("段一\n\n段二\n\n段三") == ["段一", "段二", "段三"]

    def test_single_newline_stays_in_paragraph(self) -> None:
        assert split_into_paragraphs("行一\n行二") == ["行一\n行二"]

    def test_empty(self) -> None:
        assert split_into_paragraphs("") == []


class TestSplitAfterPunctuation:
    def test_basic_sentence_split(self) -> None:
        text = "今天天气好。我们出门吧！要带伞吗？"
        assert split_after_punctuation(text, SENTENCE_ENDERS) == [
            "今天天气好。",
            "我们出门吧！",
            "要带伞吗？",
        ]

    def test_mixed_chinese_english_punctuation(self) -> None:
        text = "He said hi! 她笑了。Really?然后呢…"
        parts = split_after_punctuation(text, SENTENCE_ENDERS)
        # 标点后的空格归下一段，所有字符无丢失
        assert parts == ["He said hi!", " 她笑了。", "Really?", "然后呢…"]
        assert "".join(parts) == text

    def test_closing_quote_stays_with_sentence(self) -> None:
        text = "他说：“走吧。”然后离开了。"
        parts = split_after_punctuation(text, SENTENCE_ENDERS)
        assert parts == ["他说：“走吧。”", "然后离开了。"]

    def test_consecutive_enders_grouped(self) -> None:
        parts = split_after_punctuation("真的吗？！太好了……结束", SENTENCE_ENDERS)
        assert parts == ["真的吗？！", "太好了……", "结束"]

    def test_secondary_punctuation(self) -> None:
        parts = split_after_punctuation("一，二；三、四", SECONDARY_ENDERS)
        assert parts == ["一，", "二；", "三、", "四"]

    def test_no_char_lost(self) -> None:
        text = "无标点结尾的句子"
        assert "".join(split_after_punctuation(text, SENTENCE_ENDERS)) == text

    def test_empty(self) -> None:
        assert split_after_punctuation("", SENTENCE_ENDERS) == []
