#!/usr/bin/env python3
"""FFmpeg manifest 校验（fail closed）。

任何占位值、缺失字段、SHA-256 不一致、版本/能力不符 → 退出码非零。
用法：python3 validate_manifest.py [--check-binaries]
    --check-binaries：同时校验 vendor 二进制（存在、SHA、版本、能力）
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent
MANIFEST = PACKAGING_DIR / "ffmpeg_manifest.json"
VENDOR = PACKAGING_DIR / "vendor"

PLACEHOLDER_PATTERN = re.compile(r"FILL_ME|REPLACE|TBD|example\.com", re.IGNORECASE)
REQUIRED_TOP_FIELDS = ("provider", "release_channel", "version",
                       "architecture", "license", "configure")
REQUIRED_ENCODERS = ("libx264", "aac")
REQUIRED_FILTERS = ("zoompan", "boxblur", "subtitles", "scale", "overlay")


def fail(message: str) -> None:
    print(f"[manifest] 校验失败：{message}", file=sys.stderr)
    sys.exit(1)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_placeholder(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        fail(f"字段 {name} 缺失或为空")
    if PLACEHOLDER_PATTERN.search(value):
        fail(f"字段 {name} 仍是占位值：{value[:60]}")


def main() -> None:
    check_binaries = "--check-binaries" in sys.argv

    if not MANIFEST.is_file():
        fail(f"找不到 {MANIFEST.name}")
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"manifest 不是合法 JSON：{exc}")

    for field in REQUIRED_TOP_FIELDS:
        check_placeholder(field, data.get(field))
    if data.get("release_channel") != "release":
        fail("release_channel 必须为 release（禁止 snapshot/nightly）")
    if data.get("version") != "8.1.2":
        fail(f"版本必须固定为 8.1.2，当前：{data.get('version')}")
    if data.get("architecture") != "arm64":
        fail("architecture 必须为 arm64")

    for name in ("ffmpeg", "ffprobe"):
        entry = data.get(name)
        if not isinstance(entry, dict):
            fail(f"缺少 {name} 条目")
        check_placeholder(f"{name}.url", entry.get("url"))
        check_placeholder(f"{name}.sha256", entry.get("sha256"))
        if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"].lower()):
            fail(f"{name}.sha256 不是合法的 SHA-256")

    correspondence = data.get("source_correspondence", {})
    sources_archived = bool(correspondence.get("archived"))

    if check_binaries:
        for name in ("ffmpeg", "ffprobe"):
            binary = VENDOR / name
            if not binary.is_file():
                fail(f"vendor 缺少 {name}（请手工下载至 packaging/vendor/）")
            actual = sha256_of(binary)
            expected = data[name]["sha256"].lower()
            if actual != expected:
                fail(f"{name} SHA-256 不一致：manifest={expected[:12]}… "
                     f"实际={actual[:12]}…")
        version_out = subprocess.run(
            [str(VENDOR / "ffmpeg"), "-version"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if version_out.returncode != 0:
            fail("vendor ffmpeg 无法执行")
        if data["version"] not in version_out.stdout.splitlines()[0]:
            fail(f"vendor ffmpeg 版本与 manifest 不符："
                 f"{version_out.stdout.splitlines()[0]}")
        encoders = subprocess.run(
            [str(VENDOR / "ffmpeg"), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
        for encoder in REQUIRED_ENCODERS:
            if f" {encoder} " not in encoders:
                fail(f"vendor ffmpeg 缺少编码器 {encoder}")
        filters = subprocess.run(
            [str(VENDOR / "ffmpeg"), "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
        for filter_name in REQUIRED_FILTERS:
            if f" {filter_name} " not in filters:
                fail(f"vendor ffmpeg 缺少滤镜 {filter_name}")

    print(f"[manifest] 校验通过（source_correspondence.archived="
          f"{sources_archived}）")
    # 供 build_app.sh 读取合规门状态
    print(f"SOURCES_ARCHIVED={'1' if sources_archived else '0'}")


if __name__ == "__main__":
    main()
