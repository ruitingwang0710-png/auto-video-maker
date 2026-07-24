"""credits.txt 生成（时间来源可注入，便于测试）。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

from auto_video_maker.models.project import Project

logger = logging.getLogger(__name__)


def _default_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class CreditsService:
    """按已裁决格式生成图片版权信息文件。"""

    def __init__(self, timestamp_provider: Callable[[], str] | None = None) -> None:
        self._timestamp = timestamp_provider or _default_timestamp

    def build_text(self, project: Project) -> str:
        lines = [
            f"项目：{project.project_name}",
            f"生成时间：{self._timestamp()}",
            "",
            "图片素材来源与许可：",
            "",
        ]
        for scene in project.scenes:
            asset = scene.selected_asset or {}
            lines.append(f"场景 {scene.scene_id}：")
            if asset.get("provider") == "local":
                lines.append(f"  标题：{asset.get('title', '')}")
                lines.append("  来源：用户提供的本地图片")
            else:
                lines.append(f"  标题：{asset.get('title', '') or '（无标题）'}")
                author = asset.get("author", "") or "未知作者"
                author_url = asset.get("author_url", "")
                lines.append(
                    f"  作者：{author}" + (f"（{author_url}）" if author_url else "")
                )
                lines.append(f"  来源页：{asset.get('source_page', '')}")
                license_name = str(asset.get("license", "")).upper()
                version = asset.get("license_version", "")
                license_url = asset.get("license_url", "")
                license_line = f"  许可证：{license_name}"
                if version:
                    license_line += f" {version}"
                if license_url:
                    license_line += f"（{license_url}）"
                lines.append(license_line)
                attribution = asset.get("attribution", "")
                if attribution:
                    lines.append(f"  署名：{attribution}")
            lines.append("")
        return "\n".join(lines)

    def generate(self, project: Project, target_path: Path) -> Path:
        """生成 credits 文本到指定路径（通常为 staging 内），返回该路径。"""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(self.build_text(project), encoding="utf-8")
        logger.info("credits 已生成：%s", target_path.name)
        return target_path
