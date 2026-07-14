"""RuleBasedSceneSplitter 拆分策略测试。

不变量：normalize_for_comparison("".join(scene_texts))
      == normalize_for_comparison(cleaned_script)
"""

import pytest

from auto_video_maker.services.scene_splitter import (
    RuleBasedSceneSplitter,
    SceneSplitter,
)
from auto_video_maker.services.script_parser import (
    clean_script,
    normalize_for_comparison,
    visible_length,
)

MIN_LEN = 15
MAX_LEN = 60


@pytest.fixture
def splitter() -> RuleBasedSceneSplitter:
    return RuleBasedSceneSplitter()


def assert_invariant(scenes: list[str], cleaned: str) -> None:
    assert normalize_for_comparison("".join(scenes)) == normalize_for_comparison(cleaned)


# 覆盖 TASK.md 测试要求 1–9 的拆分部分

def test_single_paragraph(splitter: RuleBasedSceneSplitter) -> None:
    cleaned = clean_script("人工智能正在改变企业的工作方式，也在改变每个人的生活。")
    scenes = splitter.split(cleaned)
    assert scenes == [cleaned]
    assert_invariant(scenes, cleaned)


def test_multi_paragraph(splitter: RuleBasedSceneSplitter) -> None:
    raw = "第一段讲人工智能的发展历史与现状。\n\n第二段讲自动化工具带来的效率提升。"
    cleaned = clean_script(raw)
    scenes = splitter.split(cleaned)
    assert len(scenes) == 2
    assert scenes[0].startswith("第一段")
    assert scenes[1].startswith("第二段")
    assert_invariant(scenes, cleaned)


def test_mixed_punctuation(splitter: RuleBasedSceneSplitter) -> None:
    raw = (
        "AI is changing the world! 许多公司开始使用自动化工具处理日常事务。"
        "Does it really work? 答案是肯定的，效率显著提高了很多倍。"
        "这是一个不可逆转的全球性趋势……大家都需要认真面对。"
    )
    cleaned = clean_script(raw)
    scenes = splitter.split(cleaned)
    assert len(scenes) >= 2
    for scene in scenes:
        assert visible_length(scene) <= MAX_LEN
    assert_invariant(scenes, cleaned)


def test_empty_script(splitter: RuleBasedSceneSplitter) -> None:
    assert splitter.split("") == []


def test_whitespace_only(splitter: RuleBasedSceneSplitter) -> None:
    assert splitter.split(clean_script("   \n\n 　　 \n ")) == []


def test_short_sentences_merged_forward(splitter: RuleBasedSceneSplitter) -> None:
    # 段落超长触发切句；短句应向后合并
    raw = "好。很好。非常好。真的非常好。今天的天气特别晴朗适合外出散步。我们决定去公园里走一走看看风景放松心情。"
    cleaned = clean_script(raw)
    scenes = splitter.split(cleaned)
    for scene in scenes:
        assert visible_length(scene) <= MAX_LEN
    # 开头的极短句不应各自成为场景
    assert visible_length(scenes[0]) >= MIN_LEN
    assert_invariant(scenes, cleaned)


def test_last_short_sentence_merges_backward(splitter: RuleBasedSceneSplitter) -> None:
    # 最后一句过短且无后句，应尝试并入前一个场景
    raw = (
        "人工智能技术在过去十年间取得了巨大的进步和发展成就。"
        "自动化工具正在帮助许多企业处理大量重复性的日常工作。"
        "效果很好。"
    )
    cleaned = clean_script(raw)
    scenes = splitter.split(cleaned)
    assert "效果很好。" in scenes[-1]
    assert visible_length(scenes[-1]) <= MAX_LEN
    # 末句已被合并，不是独立短场景
    assert scenes[-1] != "效果很好。"
    assert_invariant(scenes, cleaned)


def test_unmergeable_short_scene_kept(splitter: RuleBasedSceneSplitter) -> None:
    # 前后两句都是 59 字（接近上限），3 字短句向前向后合并都会超过 60 字，
    # 此时允许保留不足 15 字的独立短场景
    long_a = "前" * 58 + "。"
    long_b = "后" * 58 + "。"
    raw = long_a + "短句。" + long_b
    cleaned = clean_script(raw)
    scenes = splitter.split(cleaned)
    assert scenes == [long_a, "短句。", long_b]
    assert visible_length(scenes[1]) < MIN_LEN
    for scene in scenes:
        assert visible_length(scene) <= MAX_LEN
    assert_invariant(scenes, cleaned)


def test_long_sentence_resplit_by_secondary_punctuation(
    splitter: RuleBasedSceneSplitter,
) -> None:
    raw = (
        "在这个飞速发展的时代里，人工智能技术正在深刻地改变着我们的生产方式，"
        "也在悄悄地改变着我们的生活习惯，无论是工作学习还是日常娱乐，"
        "它的影响已经渗透到了社会的每一个角落，没有人能够置身事外。"
    )
    cleaned = clean_script(raw)
    assert visible_length(cleaned) > MAX_LEN
    scenes = splitter.split(cleaned)
    assert len(scenes) >= 2
    for scene in scenes:
        assert visible_length(scene) <= MAX_LEN
    assert_invariant(scenes, cleaned)


def test_long_text_without_any_punctuation_hard_split(
    splitter: RuleBasedSceneSplitter,
) -> None:
    cleaned = "字" * 150
    scenes = splitter.split(cleaned)
    assert len(scenes) == 3
    assert [visible_length(scene) for scene in scenes] == [60, 60, 30]
    assert_invariant(scenes, cleaned)


def test_order_preserved(splitter: RuleBasedSceneSplitter) -> None:
    raw = "\n\n".join(f"第{i}段的内容讲述了完全不同的主题与故事。" for i in range(1, 6))
    cleaned = clean_script(raw)
    scenes = splitter.split(cleaned)
    positions = [cleaned.find(scene[:5]) for scene in scenes]
    assert positions == sorted(positions)
    assert_invariant(scenes, cleaned)


def test_no_loss_no_duplication_invariant_battery(
    splitter: RuleBasedSceneSplitter,
) -> None:
    samples = [
        "人工智能正在改变企业的工作方式。过去，许多工作需要员工手动完成。"
        "现在，自动化工具可以协助企业处理重复任务，提高工作效率。",
        "重复重复重复。重复重复重复。重复重复重复。",  # 原文本身含重复
        "短。\n\n也短。\n\n还是短。",
        "A quick brown fox! 中文混排测试，包含english words和数字123。结束了吗？没有……",
        "字" * 200,
        "他说：“今天很好。”然后走了。\n\n第二段又说：“明天更好！”这就是全部。",
    ]
    for raw in samples:
        cleaned = clean_script(raw)
        scenes = splitter.split(cleaned)
        assert_invariant(scenes, cleaned)


def test_deterministic(splitter: RuleBasedSceneSplitter) -> None:
    raw = "第一段内容较长需要被拆分。它有很多句子！真的很多？\n\n第二段。"
    cleaned = clean_script(raw)
    assert splitter.split(cleaned) == splitter.split(cleaned)


def test_is_scene_splitter_subclass(splitter: RuleBasedSceneSplitter) -> None:
    assert isinstance(splitter, SceneSplitter)


def test_invalid_thresholds_rejected() -> None:
    with pytest.raises(ValueError):
        RuleBasedSceneSplitter(min_length=0)
    with pytest.raises(ValueError):
        RuleBasedSceneSplitter(min_length=60, max_length=15)
