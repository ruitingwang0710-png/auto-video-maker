"""集成测试：创建 → 修改 → 保存 → 重新打开的完整流程。"""

from pathlib import Path

from auto_video_maker.models.project import Scene
from auto_video_maker.services.project_manager import PROJECT_FILE_NAME, ProjectManager

SCRIPT = "人工智能正在改变企业的工作方式。\n\n自动化工具可以协助企业处理重复任务，提高工作效率。"


def test_full_project_lifecycle(tmp_path: Path) -> None:
    manager = ProjectManager()
    output_dir = tmp_path / "桌面" / "视频项目"

    # 创建
    project = manager.create_project("完整流程测试", SCRIPT, "9:16", output_dir)
    project_dir = output_dir / "完整流程测试"
    assert (project_dir / PROJECT_FILE_NAME).is_file()

    # 修改：添加场景后再保存
    project.scenes.append(
        Scene(
            scene_id=1,
            text="人工智能正在改变企业的工作方式。",
            search_keywords=["artificial intelligence office"],
            selected_asset={
                "provider": "openverse",
                "asset_id": "123",
                "local_path": "assets/scene_001.jpg",
                "source_page": "https://example.com/photo/123",
                "author": "Example Author",
                "license": "CC BY 4.0",
            },
        )
    )
    first_updated_at = project.updated_at
    manager.save_project(project)

    # 重新打开
    reloaded = manager.load_project(project_dir)
    assert reloaded.project_id == project.project_id
    assert reloaded.original_script == SCRIPT
    assert len(reloaded.scenes) == 1
    scene = reloaded.scenes[0]
    assert scene.selected_asset is not None
    assert scene.selected_asset["provider"] == "openverse"
    assert scene.selected_asset["license"] == "CC BY 4.0"
    assert reloaded.updated_at >= first_updated_at
