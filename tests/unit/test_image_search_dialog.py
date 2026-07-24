"""ImageSearchDialog 关键词消费行为测试。

- 不访问真实 LLM 或 Openverse（FakeLLMClient / FakeImageProvider）
- QMessageBox 被 monkeypatch，避免模态阻塞
- 无法加载 Qt 图形库的环境自动跳过（在 macOS 上执行）
"""

import json
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets",
    reason="当前环境无法加载 PySide6/Qt 图形库",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from auto_video_maker.infrastructure.config import ConfigStore, LLMSettings  # noqa: E402
from auto_video_maker.infrastructure.task_runner import TaskRunner  # noqa: E402
from auto_video_maker.providers.image_provider import ImageCandidate, ImageProvider  # noqa: E402
from auto_video_maker.providers.llm_client import LLMClient  # noqa: E402
from auto_video_maker.services.asset_download_service import AssetDownloadService  # noqa: E402
from auto_video_maker.services.keyword_service import KeywordService  # noqa: E402
from auto_video_maker.services.smart_split_service import LLMAvailability  # noqa: E402
from auto_video_maker.ui.image_search_dialog import ImageSearchDialog  # noqa: E402

SCENE_TEXT = "人们正在使用视频剪辑软件处理素材。"
THREE_KEYWORDS = ["people editing video", "video editing process", "editing software interface"]


class FakeImageProvider(ImageProvider):
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, per_page: int = 12) -> list[ImageCandidate]:
        self.queries.append(query)
        return []


class FakeLLMClient(LLMClient):
    def __init__(self, response: str) -> None:
        self.response = response

    def send(self, prompt: str, response_format: dict | None = None) -> str:
        return self.response


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """QMessageBox 静态方法改为记录调用，防止测试中模态阻塞。"""
    calls: list[tuple[str, str]] = []

    def record(kind):
        def _record(parent, title, text, *args, **kwargs):
            calls.append((kind, f"{title}: {text}"))
            return QMessageBox.StandardButton.Ok

        return _record

    monkeypatch.setattr(QMessageBox, "information", record("information"))
    monkeypatch.setattr(QMessageBox, "warning", record("warning"))
    monkeypatch.setattr(QMessageBox, "question", record("question"))
    return calls


def wait_until(qapp: QApplication, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def make_dialog(
    qapp: QApplication,
    tmp_path: Path,
    llm_response: str = json.dumps(THREE_KEYWORDS),
    llm_available: bool = True,
    initial_keywords: list[str] | None = None,
) -> tuple[ImageSearchDialog, FakeImageProvider, TaskRunner, dict]:
    config_store = ConfigStore(tmp_path / "config.json")
    config_store.save(LLMSettings(enabled=True, base_url="https://a.com/v1", model="m"))
    response_ref = {"value": llm_response}  # 可在测试中途更换模型响应
    keyword_service = KeywordService(
        config_store,
        availability_check=lambda: LLMAvailability(
            llm_available, "" if llm_available else "未配置"
        ),
        llm_client_factory=lambda settings: FakeLLMClient(response_ref["value"]),
    )
    provider = FakeImageProvider()
    runner = TaskRunner()
    dialog = ImageSearchDialog(
        scene_text=SCENE_TEXT,
        initial_keywords=list(initial_keywords or []),
        project_root=tmp_path,
        image_provider=provider,
        download_service=AssetDownloadService(),
        keyword_service=keyword_service,
        task_runner=runner,
        privacy_gate=None,
    )
    return dialog, provider, runner, response_ref


def run_ai_generation(qapp, dialog: ImageSearchDialog) -> None:
    """同步驱动一次 AI 生成，确定性地等待回调完成。

    等待条件必须是「任务 ID 归零」：fallback 推荐按钮在构造时就存在，
    以按钮存在与否作条件会在旧状态上提前返回（此前 macOS 失败的根因）。
    """
    dialog._on_ai_keywords()
    assert dialog._active_task_id is not None  # 任务已提交
    assert wait_until(qapp, lambda: dialog._active_task_id is None)  # 回调已处理


# 测试要求 1：三个关键词 → 三个可选项、默认第一个、无逗号拼接
def test_three_keywords_displayed_first_prefilled(qapp, tmp_path) -> None:
    dialog, provider, *_ = make_dialog(qapp, tmp_path)
    run_ai_generation(qapp, dialog)
    assert [button.text() for button in dialog.suggestion_buttons] == THREE_KEYWORDS
    assert dialog.keyword_edit.text() == THREE_KEYWORDS[0]
    assert "," not in dialog.keyword_edit.text()
    # 生成后不得自动搜索（测试要求 8/9）
    assert provider.queries == []
    dialog.close()


# 测试要求 2：点击第二个关键词 → 搜索框变为第二个
def test_click_second_suggestion_updates_box(qapp, tmp_path) -> None:
    dialog, *_ = make_dialog(qapp, tmp_path)
    run_ai_generation(qapp, dialog)
    dialog.suggestion_buttons[1].click()
    assert dialog.keyword_edit.text() == THREE_KEYWORDS[1]
    # 点击推荐词不改动持久化列表
    assert dialog.edited_keywords == THREE_KEYWORDS
    dialog.close()


# 测试要求 3：搜索只提交当前搜索框内容
def test_search_submits_only_current_box_content(qapp, tmp_path) -> None:
    dialog, provider, *_ = make_dialog(qapp, tmp_path)
    run_ai_generation(qapp, dialog)
    dialog.suggestion_buttons[1].click()
    dialog._on_search()
    assert wait_until(qapp, lambda: provider.queries)
    assert provider.queries == [THREE_KEYWORDS[1]]
    # 其他关键词不得混入
    assert THREE_KEYWORDS[0] not in provider.queries[0]
    assert THREE_KEYWORDS[2] not in provider.queries[0]
    dialog.close()


# 测试要求 4：手动编辑后搜索用编辑内容
def test_manual_edit_used_for_search(qapp, tmp_path) -> None:
    dialog, provider, *_ = make_dialog(qapp, tmp_path)
    run_ai_generation(qapp, dialog)
    dialog.keyword_edit.setText("  handmade query  ")
    dialog._on_search()
    assert wait_until(qapp, lambda: provider.queries)
    assert provider.queries == ["handmade query"]
    dialog.close()


# 测试要求 5：规范化用例
def test_ai_keywords_normalized(qapp, tmp_path) -> None:
    response = json.dumps(["video editing", "", "VIDEO EDITING", "editing software"])
    dialog, *_ = make_dialog(qapp, tmp_path, llm_response=response)
    run_ai_generation(qapp, dialog)
    assert [b.text() for b in dialog.suggestion_buttons] == ["video editing", "editing software"]
    assert dialog.edited_keywords == ["video editing", "editing software"]
    dialog.close()


# 测试要求 6：AI 返回空/全空白 → 保留搜索框、不改列表、友好错误
def test_empty_ai_result_keeps_state(qapp, tmp_path, no_modal_dialogs) -> None:
    dialog, *_ = make_dialog(
        qapp, tmp_path, initial_keywords=["saved keyword"]
    )
    dialog.keyword_edit.setText("user typed")
    dialog._on_ai_keywords_done([])  # 直接驱动空结果路径
    assert dialog.keyword_edit.text() == "user typed"  # 搜索框未被清空
    assert dialog.edited_keywords == ["saved keyword"]  # 列表未被修改
    assert any("没有可用关键词" in message for _, message in no_modal_dialogs)
    dialog.close()


def test_ai_failure_keeps_state(qapp, tmp_path, no_modal_dialogs) -> None:
    """LLM 返回非法 JSON：错误提示，状态不变。"""
    dialog, *_ = make_dialog(
        qapp, tmp_path, llm_response="不是 JSON", initial_keywords=["saved keyword"]
    )
    dialog.keyword_edit.setText("user typed")
    dialog._on_ai_keywords()
    assert wait_until(qapp, lambda: no_modal_dialogs)
    assert dialog.keyword_edit.text() == "user typed"
    assert dialog.edited_keywords == ["saved keyword"]
    dialog.close()


# 测试要求 7：单关键词
def test_single_keyword(qapp, tmp_path) -> None:
    dialog, *_ = make_dialog(qapp, tmp_path, llm_response=json.dumps(["only one"]))
    run_ai_generation(qapp, dialog)
    assert [b.text() for b in dialog.suggestion_buttons] == ["only one"]
    assert dialog.keyword_edit.text() == "only one"
    dialog.close()


# 测试要求 8：已保存关键词重新展示
def test_saved_keywords_shown_on_reopen(qapp, tmp_path) -> None:
    saved = ["saved one", "saved two"]
    dialog, provider, *_ = make_dialog(qapp, tmp_path, initial_keywords=saved)
    assert [b.text() for b in dialog.suggestion_buttons] == saved
    assert dialog.keyword_edit.text() == "saved one"
    assert dialog.edited_keywords == saved  # 完整列表保留
    # 打开时不自动搜索
    assert provider.queries == []
    dialog.close()


def test_blank_query_not_submitted(qapp, tmp_path, no_modal_dialogs) -> None:
    dialog, provider, *_ = make_dialog(qapp, tmp_path)
    dialog.keyword_edit.setText("   ")
    dialog._on_search()
    qapp.processEvents()
    assert provider.queries == []
    assert any("缺少关键词" in message for _, message in no_modal_dialogs)
    dialog.close()


# ------------------------------------------------------------ 状态替换回归测试


def test_fallback_replaced_by_two_ai_keywords(qapp, tmp_path) -> None:
    """回归 1：初始 fallback + AI 两个关键词 → 只剩两个 AI 关键词。"""
    response = json.dumps(["video editing", "editing software"])
    dialog, *_ = make_dialog(qapp, tmp_path, llm_response=response)
    # 初始状态：无保存关键词，展示中文 fallback
    fallback_text = dialog.suggestion_buttons[0].text()
    assert "视频剪辑" in fallback_text
    run_ai_generation(qapp, dialog)
    # 整体替换，绝不合并 fallback
    assert dialog.edited_keywords == ["video editing", "editing software"]
    assert [b.text() for b in dialog.suggestion_buttons] == ["video editing", "editing software"]
    assert fallback_text not in dialog.edited_keywords
    assert all(fallback_text != b.text() for b in dialog.suggestion_buttons)
    dialog.close()


def test_fallback_replaced_by_single_ai_keyword(qapp, tmp_path) -> None:
    """回归 2：初始 fallback + AI 单关键词 → edited_keywords 只含该关键词。"""
    dialog, *_ = make_dialog(qapp, tmp_path, llm_response=json.dumps(["only one"]))
    fallback_text = dialog.suggestion_buttons[0].text()
    run_ai_generation(qapp, dialog)
    assert dialog.edited_keywords == ["only one"]
    assert fallback_text not in dialog.edited_keywords
    # 回归 3：搜索框等于第一个（唯一）AI 关键词
    assert dialog.keyword_edit.text() == "only one"
    dialog.close()


def test_ai_failure_keeps_fallback_suggestions(qapp, tmp_path, no_modal_dialogs) -> None:
    """回归 4：AI 失败时，fallback 推荐与搜索框均保持不变。"""
    dialog, *_ = make_dialog(qapp, tmp_path, llm_response="不是 JSON")
    fallback_buttons = [b.text() for b in dialog.suggestion_buttons]
    box_before = dialog.keyword_edit.text()
    keywords_before = list(dialog.edited_keywords)
    run_ai_generation(qapp, dialog)
    assert [b.text() for b in dialog.suggestion_buttons] == fallback_buttons
    assert dialog.keyword_edit.text() == box_before
    assert dialog.edited_keywords == keywords_before
    assert no_modal_dialogs  # 有友好错误提示
    dialog.close()


def test_second_ai_generation_replaces_first(qapp, tmp_path) -> None:
    """回归 5：AI 多次成功生成，第二次结果替换第一次，不得累积。"""
    dialog, _, _, response_ref = make_dialog(
        qapp, tmp_path, llm_response=json.dumps(["first a", "first b"])
    )
    run_ai_generation(qapp, dialog)
    assert dialog.edited_keywords == ["first a", "first b"]

    response_ref["value"] = json.dumps(["second only"])
    run_ai_generation(qapp, dialog)
    assert dialog.edited_keywords == ["second only"]
    assert [b.text() for b in dialog.suggestion_buttons] == ["second only"]
    assert dialog.keyword_edit.text() == "second only"
    dialog.close()
