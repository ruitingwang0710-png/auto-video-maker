"""场景业务逻辑：Scene 的统一创建、编辑操作、覆盖保护与未保存状态。

- Scene 只能由本服务创建（拆分器只返回 list[str]）
- 只依赖 SceneSplitter 接口，不依赖具体实现（由 app.py 注入）
- UI 不得绕过本服务直接操作 project.scenes 或读写 JSON
"""

from __future__ import annotations

import logging

from auto_video_maker.models.project import Project, Scene
from auto_video_maker.services.project_manager import ProjectManager, ProjectManagerError
from auto_video_maker.services.scene_splitter import SceneSplitter
from auto_video_maker.services.script_parser import clean_script

logger = logging.getLogger(__name__)


class SceneServiceError(Exception):
    """场景操作失败。消息面向普通用户，可直接展示。"""


class ScenesExistError(SceneServiceError):
    """项目已有场景，拆分需要用户明确确认覆盖。"""


class SceneService:
    """场景生成与编辑服务。"""

    def __init__(self, splitter: SceneSplitter, project_manager: ProjectManager) -> None:
        self._splitter = splitter
        self._project_manager = project_manager
        self._dirty = False

    # ------------------------------------------------------------ 状态

    @property
    def is_dirty(self) -> bool:
        """是否存在未保存的场景修改。"""
        return self._dirty

    def discard_changes(self) -> None:
        """放弃未保存标记（内存数据由调用方决定如何处理）。"""
        self._dirty = False

    # ------------------------------------------------------------ 拆分

    def split_script(self, project: Project, *, overwrite: bool = False) -> list[Scene]:
        """从 original_script 生成场景并写入 project.scenes。

        已有场景且未确认覆盖时抛出 ScenesExistError，绝不静默覆盖。
        original_script 保持原样，清理只作用于拆分用副本。
        """
        if project.scenes and not overwrite:
            raise ScenesExistError(
                f"项目已有 {len(project.scenes)} 个场景。"
                "重新拆分将覆盖现有场景，请确认后再操作。"
            )
        cleaned = clean_script(project.original_script)
        texts = self._splitter.split(cleaned)
        if not texts:
            raise SceneServiceError("文案中没有可拆分的内容。")
        project.scenes = self.build_scenes(texts)
        self._dirty = True
        logger.info("文案拆分完成：%d 个场景", len(project.scenes))
        return project.scenes

    def replace_from_texts(
        self, project: Project, texts: list[str], *, overwrite: bool = False
    ) -> list[Scene]:
        """将已确认的场景文字列表应用为项目场景（唯一写入口）。

        防御性验证：即使上游已校验，此处仍完整验证；
        验证失败时 Project、scenes、dirty 状态均不变化。
        先完整验证并构建全部 Scene，再一次性替换。
        """
        if not isinstance(texts, list) or not texts:
            raise SceneServiceError("拆分结果为空，无法应用。")
        for item in texts:
            if not isinstance(item, str) or not item.strip():
                raise SceneServiceError("拆分结果包含空白或非文本场景，已拒绝应用。")
        if project.scenes and not overwrite:
            raise ScenesExistError(
                f"项目已有 {len(project.scenes)} 个场景。"
                "应用新的拆分结果将覆盖现有场景，请确认后再操作。"
            )
        scenes = self.build_scenes(texts)  # 先完整构建
        project.scenes = scenes  # 再一次性替换
        self._dirty = True
        logger.info("已应用拆分结果：%d 个场景", len(scenes))
        return scenes

    def build_scenes(self, texts: list[str]) -> list[Scene]:
        """将场景文字列表统一转换为 Scene 列表（默认字段见 TASK.md）。"""
        return [
            Scene(
                scene_id=index,
                text=text,
                search_keywords=[],
                selected_asset=None,
                audio_path=None,
                duration=None,
                status="pending",
            )
            for index, text in enumerate(texts, start=1)
        ]

    # ------------------------------------------------------------ 编辑

    def update_scene_text(self, project: Project, index: int, text: str) -> None:
        """更新指定位置（0 起）场景的文字。"""
        scene = self._scene_at(project, index)
        if scene.text != text:
            scene.text = text
            self._dirty = True

    def add_scene(self, project: Project, text: str = "") -> Scene:
        """在列表末尾新增场景并重新编号。"""
        scene = Scene(scene_id=len(project.scenes) + 1, text=text)
        project.scenes.append(scene)
        self.renumber(project)
        self._dirty = True
        return scene

    def delete_scene(self, project: Project, index: int) -> None:
        """删除指定位置（0 起）的场景并重新编号。"""
        self._scene_at(project, index)
        del project.scenes[index]
        self.renumber(project)
        self._dirty = True

    def move_scene_up(self, project: Project, index: int) -> int:
        """上移场景，返回新位置。第一项上移为安全 no-op。"""
        self._scene_at(project, index)
        if index == 0:
            return index
        scenes = project.scenes
        scenes[index - 1], scenes[index] = scenes[index], scenes[index - 1]
        self.renumber(project)
        self._dirty = True
        return index - 1

    def move_scene_down(self, project: Project, index: int) -> int:
        """下移场景，返回新位置。最后一项下移为安全 no-op。"""
        self._scene_at(project, index)
        if index >= len(project.scenes) - 1:
            return index
        scenes = project.scenes
        scenes[index], scenes[index + 1] = scenes[index + 1], scenes[index]
        self.renumber(project)
        self._dirty = True
        return index + 1

    def renumber(self, project: Project) -> None:
        """scene_id 重新从 1 连续编号。"""
        for index, scene in enumerate(project.scenes, start=1):
            scene.scene_id = index

    # ------------------------------------------------------------ 保存

    def save(self, project: Project) -> None:
        """校验并保存项目，成功后清除未保存状态。

        空白场景文字不得保存。
        """
        for scene in project.scenes:
            if not scene.text.strip():
                raise SceneServiceError(
                    f"第 {scene.scene_id} 个场景内容为空。"
                    "请填写场景文字，或删除该场景后再保存。"
                )
        try:
            self._project_manager.save_project(project)
        except ProjectManagerError as exc:
            raise SceneServiceError(f"保存失败：{exc}") from exc
        self._dirty = False

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _scene_at(project: Project, index: int) -> Scene:
        if not 0 <= index < len(project.scenes):
            raise SceneServiceError("所选场景不存在，请重新选择。")
        return project.scenes[index]
