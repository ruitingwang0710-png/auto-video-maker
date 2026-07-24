"""项目管理：创建、保存、打开项目。

项目工作目录结构（见 TASK.md）：

    输出目录/项目名称/
    ├── project.json
    ├── assets/
    ├── audio/
    ├── subtitles/
    ├── temp/
    ├── output/
    └── logs/
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from auto_video_maker.models.project import (
    ASPECT_RATIO_RESOLUTIONS,
    Project,
    ProjectSettings,
    now_iso,
)
from auto_video_maker.utils.validation import (
    ValidationError,
    ensure_within_directory,
    ensure_writable_directory,
    validate_project_name,
    validate_script,
)

logger = logging.getLogger(__name__)

PROJECT_FILE_NAME = "project.json"
PROJECT_SUBDIRS = ("assets", "audio", "subtitles", "temp", "output", "logs")


class ProjectManagerError(Exception):
    """项目管理操作失败。消息面向普通用户，可直接展示。"""


class ProjectManager:
    """负责项目的创建、保存与打开。"""

    def project_directory(self, project: Project) -> Path:
        """返回项目的工作目录路径。"""
        return Path(project.settings.output_directory) / project.project_name

    def create_project(
        self,
        project_name: str,
        original_script: str,
        aspect_ratio: str,
        output_directory: str | os.PathLike[str],
        voice: str = "default",
        speech_rate: str = "+0%",
    ) -> Project:
        """校验输入、创建项目目录结构并保存 project.json，返回 Project。"""
        try:
            name = validate_project_name(project_name)
            script = validate_script(original_script)
            if not str(output_directory).strip():
                raise ValidationError("输出目录不能为空。")
            if aspect_ratio not in ASPECT_RATIO_RESOLUTIONS:
                supported = "、".join(ASPECT_RATIO_RESOLUTIONS)
                raise ValidationError(f"不支持的视频比例：{aspect_ratio}。支持：{supported}。")
            output_dir = ensure_writable_directory(Path(output_directory))
            project_dir = output_dir / name
            ensure_within_directory(project_dir, output_dir)
        except ValidationError as exc:
            raise ProjectManagerError(str(exc)) from exc

        if (project_dir / PROJECT_FILE_NAME).exists():
            raise ProjectManagerError(
                f"目录 {project_dir} 中已存在项目。请更换项目名称或输出目录。"
            )

        from auto_video_maker.models.project import _sanitize_speech_rate

        settings = ProjectSettings(
            aspect_ratio=aspect_ratio,
            resolution=ASPECT_RATIO_RESOLUTIONS[aspect_ratio],
            voice=voice if voice in ("female", "male", "default") else "default",
            subtitle_enabled=True,
            output_directory=str(output_dir),
            speech_rate=_sanitize_speech_rate(speech_rate),
        )
        project = Project(
            project_name=name,
            original_script=script,
            settings=settings,
        )

        try:
            project_dir.mkdir(parents=True, exist_ok=True)
            for subdir in PROJECT_SUBDIRS:
                (project_dir / subdir).mkdir(exist_ok=True)
        except OSError as exc:
            raise ProjectManagerError(
                f"无法创建项目目录：{project_dir}（{exc.strerror or exc}）。"
            ) from exc

        self.save_project(project)
        logger.info("项目已创建: %s (%s)", project.project_name, project_dir)
        return project

    def save_project(self, project: Project) -> Path:
        """将项目保存为 project.json（UTF-8，保留中文，原子写入），返回文件路径。"""
        project_dir = self.project_directory(project)
        if not project_dir.is_dir():
            raise ProjectManagerError(f"项目目录不存在：{project_dir}。")
        project.updated_at = now_iso()
        project_file = project_dir / PROJECT_FILE_NAME
        temp_file = project_dir / (PROJECT_FILE_NAME + ".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as handle:
                json.dump(project.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_file, project_file)
        except OSError as exc:
            temp_file.unlink(missing_ok=True)
            raise ProjectManagerError(
                f"保存项目失败：{project_file}（{exc.strerror or exc}）。"
            ) from exc
        logger.info("项目已保存: %s", project_file)
        return project_file

    # ------------------------------------------------------------ 项目级输出状态

    def set_subtitle_path(self, project: Project, subtitle_path: str) -> None:
        """写入 Project.output.subtitle_path（项目级状态，相对路径）。"""
        self._validate_relative_output_path(project, subtitle_path)
        project.output["subtitle_path"] = subtitle_path
        logger.info("字幕引用已写入: %s", subtitle_path)

    def clear_subtitle_path(self, project: Project) -> None:
        """清空字幕引用（派生产物失效规则）。"""
        if project.output.get("subtitle_path"):
            project.output["subtitle_path"] = None
            logger.info("字幕引用已失效")

    def set_video_path(self, project: Project, video_path: str) -> None:
        """写入 Project.output.video_path（项目级状态，相对路径）。"""
        self._validate_relative_output_path(project, video_path)
        project.output["video_path"] = video_path
        project.output["status"] = "rendered"
        logger.info("视频引用已写入: %s", video_path)

    def clear_video_path(self, project: Project) -> None:
        """清空视频引用（派生产物失效矩阵）。"""
        if project.output.get("video_path"):
            project.output["video_path"] = None
            project.output["status"] = "draft"
            logger.info("视频引用已失效")

    def _validate_relative_output_path(self, project: Project, path_str: str) -> None:
        from pathlib import PurePosixPath

        if not isinstance(path_str, str) or not path_str.strip():
            raise ProjectManagerError("输出文件路径不能为空。")
        if "\\" in path_str:
            raise ProjectManagerError("输出文件路径不能包含反斜杠。")
        pure = PurePosixPath(path_str)
        if pure.is_absolute() or Path(path_str).is_absolute():
            raise ProjectManagerError("输出文件路径必须是相对于项目目录的路径。")
        if any(part == ".." for part in pure.parts):
            raise ProjectManagerError("输出文件路径不能包含 '..'。")
        project_dir = self.project_directory(project).resolve()
        resolved = (project_dir / path_str).resolve()
        if not resolved.is_relative_to(project_dir):
            raise ProjectManagerError("输出文件路径指向项目目录之外，已拒绝。")

    def load_project(self, path: str | os.PathLike[str]) -> Project:
        """从 project.json 文件或项目目录打开项目。"""
        target = Path(path).expanduser()
        if target.is_dir():
            target = target / PROJECT_FILE_NAME
        if not target.is_file():
            raise ProjectManagerError(f"找不到项目文件：{target}。")
        try:
            with open(target, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ProjectManagerError(f"项目文件已损坏，无法解析：{target}。") from exc
        except OSError as exc:
            raise ProjectManagerError(
                f"无法读取项目文件：{target}（{exc.strerror or exc}）。"
            ) from exc
        try:
            project = Project.from_dict(data)
        except (ValueError, KeyError, TypeError) as exc:
            raise ProjectManagerError(f"项目文件内容无效：{target}（{exc}）。") from exc
        logger.info("项目已打开: %s (%s)", project.project_name, target)
        return project
