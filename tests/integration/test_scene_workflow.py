"""集成测试：创建项目 → 拆分 → 编辑 → 排序 → 保存 → 重新打开的完整流程。

使用真实的 RuleBasedSceneSplitter + ProjectManager。
"""

from pathlib import Path

import pytest

from auto_video_maker.services.project_manager import ProjectManager
from auto_video_maker.services.scene_service import SceneService, ScenesExistError
from auto_video_maker.services.scene_splitter import RuleBasedSceneSplitter
from auto_video_maker.services.script_parser import clean_script, normalize_for_comparison

SCRIPT = (
    "人工智能正在改变企业的工作方式。过去，许多工作需要员工手动完成，"
    "既耗时又容易出现各种错误。\n\n"
    "现在，自动化工具可以协助企业处理重复任务，显著提高整体工作效率。"
    "员工可以把精力集中在更有创造性的工作上。\n\n"
    "未来已来。"
)


def test_full_scene_workflow(tmp_path: Path) -> None:
    manager = ProjectManager()
    service = SceneService(RuleBasedSceneSplitter(), manager)

    # 创建项目
    project = manager.create_project("端到端场景", SCRIPT, "9:16", tmp_path)
    assert project.scenes == []

    # 拆分（首次需要明确操作，由调用方触发）
    scenes = service.split_script(project)
    assert len(scenes) >= 3
    assert normalize_for_comparison("".join(s.text for s in scenes)) == \
        normalize_for_comparison(clean_script(SCRIPT))

    # 再次拆分被覆盖保护拦截
    with pytest.raises(ScenesExistError):
        service.split_script(project)

    # 编辑、新增、排序
    service.update_scene_text(project, 0, "（已编辑）" + project.scenes[0].text)
    service.add_scene(project, "手动补充的结尾场景。")
    service.move_scene_up(project, len(project.scenes) - 1)

    # 保存
    assert service.is_dirty
    service.save(project)
    assert not service.is_dirty

    # 重新打开：数据完整
    reloaded = manager.load_project(tmp_path / "端到端场景")
    assert reloaded.original_script == SCRIPT  # 原文永远保留原样
    assert len(reloaded.scenes) == len(project.scenes)
    assert [s.scene_id for s in reloaded.scenes] == list(range(1, len(project.scenes) + 1))
    assert [s.text for s in reloaded.scenes] == [s.text for s in project.scenes]
    assert reloaded.scenes[0].text.startswith("（已编辑）")
    assert any(s.text == "手动补充的结尾场景。" for s in reloaded.scenes)

    # 确认覆盖后重新拆分
    scenes_again = service.split_script(reloaded, overwrite=True)
    assert normalize_for_comparison("".join(s.text for s in scenes_again)) == \
        normalize_for_comparison(clean_script(SCRIPT))
