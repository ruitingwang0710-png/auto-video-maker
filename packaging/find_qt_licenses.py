#!/usr/bin/env python3
"""定位并校验 PySide6/Qt 的 LGPL 全文，注入 App 的 licenses 目录（fail closed）。

设计目标
--------
- 不硬编码单一 site-packages 路径：依次检查多个 PySide6 相关 distribution，
  并在每个 distribution 的多个惯用位置（dist-info/licenses/、dist-info/、
  包目录 LICENSES/、RECORD 声明文件）中查找。
- 不按文件名盲信：候选文件必须读出内容，确认包含
  "GNU LESSER GENERAL PUBLIC LICENSE"，且版本与该 distribution 声明的
  LGPL 版本（来自 METADATA 的 SPDX License 表达式）一致。
- fail closed：找不到 / 内容不符 / 文件为空 / 复制后与来源不一致 → 非零退出。
- 绝不联网下载、绝不自行生成或手写 LGPL 全文；许可证只能来自当前构建
  环境已安装的 distribution。

该模块同时可作为库被测试导入：核心逻辑（内容校验、候选收集、复制校验）
均为可注入、可脱离真实 PySide6 安装运行的纯函数 / 适配器。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

# ---------------------------------------------------------------- 常量
# 按优先级依次检查的 distribution 名称（PySide6 可能被拆分为多个包）。
DIST_NAMES: tuple[str, ...] = (
    "PySide6",
    "PySide6_Essentials",
    "PySide6_Addons",
    "shiboken6",
)

# LGPL 全文的判定标题（大小写不敏感比较时统一转大写）。
LGPL_TITLE = "GNU LESSER GENERAL PUBLIC LICENSE"

# 文件名候选 token（仅用于收集候选，绝不作为接受依据）。
NAME_TOKENS: tuple[str, ...] = ("lgpl", "license", "licence", "copying")

# 注入目标文件名（LGPL 全文）。
LGPL_OUTPUT_NAME = "LGPL-3.0.txt"

# GPL 全文（LGPLv3 引用 GPLv3 条款，发布包中必须同时包含）。
GPL_TITLE = "GNU GENERAL PUBLIC LICENSE"
GPL_OUTPUT_NAME = "GPL-3.0.txt"
V3_MARKER = "VERSION 3, 29 JUNE 2007"

# 注入的通告与校验记录文件名。
NOTICE_OUTPUT_NAME = "PYSIDE6-NOTICE.txt"
RECORD_OUTPUT_NAME = "licenses_record.sha256"

# 仓库 vendored fallback 的 manifest 必需字段与占位符模式。
VENDORED_MANIFEST_NAME = "license_manifest.json"
REQUIRED_MANIFEST_KEYS = ("file", "source_url", "sha256", "license_id",
                          "retrieved", "provenance")
PLACEHOLDER_RE = re.compile(r"FILL_ME|REPLACE|TBD", re.IGNORECASE)

# 单个候选文件最大读取字节数（LGPL 全文约 7KB；防止误读巨大二进制）。
MAX_CANDIDATE_BYTES = 512 * 1024


# ---------------------------------------------------------------- 数据结构
@dataclass
class DistributionView:
    """对一个已安装 distribution 的最小只读视图（便于测试注入）。

    - name：distribution 名称。
    - declared_license：METADATA 的 License / License-Expression（SPDX）。
    - package_dirs：该 distribution 的顶层导入目录（用于扫描包内 LICENSES/）。
    - dist_info_dir：*.dist-info 目录（用于扫描 dist-info/licenses/）。
    - recorded_files：RECORD/`Distribution.files` 声明的绝对路径。
    """

    name: str
    declared_license: str = ""
    package_dirs: list[Path] = field(default_factory=list)
    dist_info_dir: Optional[Path] = None
    recorded_files: list[Path] = field(default_factory=list)


@dataclass
class LicenseHit:
    """一个通过内容校验的许可证文件命中。"""

    dist_name: str
    source: Path
    text: str
    is_lgpl: bool


class LicenseError(RuntimeError):
    """fail-closed 错误：任何一步不满足要求即抛出。"""


# ---------------------------------------------------------------- 纯函数：内容校验
def looks_like_license_name(path: Path) -> bool:
    """文件名是否像许可证文件（仅用于收集候选）。"""
    low = path.name.lower()
    return any(tok in low for tok in NAME_TOKENS)


def expected_lgpl_versions(declared_license: str) -> set[str]:
    """从 SPDX License 表达式解析期望的 LGPL 版本号集合。

    例如 "LGPL-3.0-only OR GPL-2.0-only" → {"3.0"}；
    "LGPL-2.1-or-later" → {"2.1"}。若表达式未声明 LGPL，返回空集合
    （此时调用方不施加版本约束，但仍要求文本本身是有效 LGPL）。
    """
    versions: set[str] = set()
    for match in re.finditer(r"LGPL-([0-9]+(?:\.[0-9]+)?)", declared_license or "",
                             flags=re.IGNORECASE):
        versions.add(match.group(1))
    return versions


def _version_marker(version: str) -> str:
    """把 SPDX 版本号（如 "3.0" / "2.1"）映射为 LGPL 正文中的版本行片段。

    LGPL-3.0 正文写作 "Version 3, 29 June 2007"；
    LGPL-2.1 正文写作 "Version 2.1, February 1999"。
    """
    # SPDX 常用 "3.0" 表示 LGPLv3；正文写 "Version 3"。
    normalized = version
    if normalized.endswith(".0"):
        normalized = normalized[:-2]
    return f"VERSION {normalized}"


def content_is_valid_lgpl(text: str, expected_versions: Iterable[str]) -> bool:
    """判断文本是否为有效的 LGPL 全文，并（若有声明）匹配期望版本。

    - 必须包含 LGPL 标题。
    - 若 expected_versions 非空，正文必须匹配其中至少一个版本行。
    - 空文本一律拒绝。
    """
    if not text or not text.strip():
        return False
    upper = text.upper()
    if LGPL_TITLE not in upper:
        return False
    wanted = list(expected_versions)
    if not wanted:
        # 未声明具体版本时，只要求是有效 LGPL 且能读到某个 "Version N" 行。
        return re.search(r"VERSION\s+[0-9]", upper) is not None
    return any(_version_marker(v) in upper for v in wanted)


def content_is_valid_gpl3(text: str) -> bool:
    """判断文本是否为有效的 GPLv3 全文。

    - 必须包含 GPL 标题与 "Version 3, 29 June 2007" 版本行
    - 不得是 LGPL（LGPL 标题出现即拒绝——LGPL 正文引用 GPL 但标题不同）
    - 空文本一律拒绝
    """
    if not text or not text.strip():
        return False
    upper = text.upper()
    if LGPL_TITLE in upper:
        return False
    return GPL_TITLE in upper and V3_MARKER in upper


def content_is_valid_lgpl3_strict(text: str) -> bool:
    """vendored fallback 的 LGPL 严格校验：标题 + v3 版本行。"""
    if not text or not text.strip():
        return False
    upper = text.upper()
    return LGPL_TITLE in upper and V3_MARKER in upper


# ---------------------------------------------------------------- 候选收集
def _iter_dir_license_files(directory: Path) -> Iterable[Path]:
    """在一个目录（浅层）中收集名字像许可证的普通文件。"""
    if not directory.is_dir():
        return
    try:
        for child in sorted(directory.iterdir()):
            if child.is_file() and looks_like_license_name(child):
                yield child
    except OSError:
        return


def collect_candidate_paths(view: DistributionView) -> list[Path]:
    """按惯用位置收集一个 distribution 的许可证候选文件（去重、保序）。

    位置（依次）：
      1. *.dist-info/licenses/        （PEP 639）
      2. *.dist-info/                  （顶层许可证文件）
      3. 包目录 LICENSES/ 与 licenses/ （Qt wheel 传统布局）
      4. 包目录顶层的许可证命名文件
      5. RECORD/`Distribution.files` 声明、且名字像许可证的文件
    """
    ordered: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            return
        seen.add(resolved)
        ordered.append(path)

    # 1 & 2：dist-info/licenses/ 与 dist-info/
    if view.dist_info_dir is not None:
        for p in _iter_dir_license_files(view.dist_info_dir / "licenses"):
            add(p)
        for p in _iter_dir_license_files(view.dist_info_dir):
            add(p)

    # 3 & 4：包目录 LICENSES/、licenses/，以及包目录顶层
    for pkg in view.package_dirs:
        for sub in ("LICENSES", "licenses"):
            for p in _iter_dir_license_files(pkg / sub):
                add(p)
        for p in _iter_dir_license_files(pkg):
            add(p)

    # 5：RECORD 声明文件
    for p in view.recorded_files:
        if looks_like_license_name(p):
            add(p)

    return ordered


# ---------------------------------------------------------------- 读取与命中
def read_candidate_text(path: Path) -> Optional[str]:
    """读取候选文件文本；空文件 / 过大 / 二进制 / 不可读 → None。"""
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size == 0 or size > MAX_CANDIDATE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:  # 含 NUL：视为二进制，跳过
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except UnicodeDecodeError:
            return None


def scan_distribution(view: DistributionView) -> tuple[Optional[LicenseHit], list[LicenseHit]]:
    """扫描单个 distribution。

    返回 (首个有效 LGPL 命中或 None, 全部有效许可证/通告命中列表)。
    "全部命中" 用于把其它 Qt/PySide6 许可证或通告一并保留。
    """
    expected = expected_lgpl_versions(view.declared_license)
    lgpl_hit: Optional[LicenseHit] = None
    all_hits: list[LicenseHit] = []
    for candidate in collect_candidate_paths(view):
        text = read_candidate_text(candidate)
        if text is None:
            continue
        is_lgpl = content_is_valid_lgpl(text, expected)
        # 非 LGPL 的许可证/通告：内容必须非空文本才保留（避免空壳文件）。
        hit = LicenseHit(view.name, candidate, text, is_lgpl)
        all_hits.append(hit)
        if is_lgpl and lgpl_hit is None:
            lgpl_hit = hit
    return lgpl_hit, all_hits


# ---------------------------------------------------------------- 真实 distribution 适配
def load_installed_view(name: str) -> Optional[DistributionView]:
    """从当前解释器已安装的 distribution 构造视图；未安装 → None。"""
    import importlib.metadata as im

    try:
        dist = im.distribution(name)
    except im.PackageNotFoundError:
        return None

    declared = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""

    # 顶层导入目录（top_level.txt / packages）。
    top_names: list[str] = []
    top_txt = dist.read_text("top_level.txt")
    if top_txt:
        top_names = [ln.strip() for ln in top_txt.splitlines() if ln.strip()]
    package_dirs: list[Path] = []
    seen_dirs: set[Path] = set()
    for tn in top_names:
        try:
            p = Path(dist.locate_file(tn))
        except Exception:
            continue
        if p.is_dir() and p not in seen_dirs:
            seen_dirs.add(p)
            package_dirs.append(p)

    # dist-info 目录：由已知文件反推。
    dist_info_dir: Optional[Path] = None
    try:
        record = dist.locate_file("")  # 通常指向 site-packages
    except Exception:
        record = None
    # 更稳妥：从 dist.files 找 *.dist-info/METADATA 的父目录
    recorded_files: list[Path] = []
    if dist.files:
        for f in dist.files:
            try:
                abs_path = Path(dist.locate_file(f))
            except Exception:
                continue
            recorded_files.append(abs_path)
            if dist_info_dir is None and abs_path.parent.name.endswith(".dist-info"):
                dist_info_dir = abs_path.parent

    if dist_info_dir is None and record is not None:
        # 回退：在 site-packages 猜测 <name>-<version>.dist-info
        base = Path(record)
        for cand in base.glob(f"{name.replace('-', '_')}-*.dist-info"):
            dist_info_dir = cand
            break

    return DistributionView(
        name=name,
        declared_license=declared,
        package_dirs=package_dirs,
        dist_info_dir=dist_info_dir,
        recorded_files=recorded_files,
    )


def load_installed_views(names: Sequence[str] = DIST_NAMES) -> list[DistributionView]:
    views: list[DistributionView] = []
    for name in names:
        view = load_installed_view(name)
        if view is not None:
            views.append(view)
    return views


# ---------------------------------------------------------------- 复制（含一致性校验）
def _safe_extra_name(dist_name: str, source: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", source.name)
    prefix = re.sub(r"[^A-Za-z0-9._-]", "_", dist_name)
    return f"{prefix}-{stem}"


def copy_verified(source_text: str, source: Path, target: Path) -> None:
    """写入并回读校验：目标非空、且内容与来源逐字节一致，否则抛错。"""
    data = source.read_bytes()
    if not data:
        raise LicenseError(f"来源文件为空：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    if not target.exists() or target.stat().st_size == 0:
        raise LicenseError(f"注入后文件缺失或为空：{target}")
    if target.read_bytes() != data:
        raise LicenseError(f"注入内容与来源不一致：{source} → {target}")


def inject_licenses(views: Sequence[DistributionView], output_dir: Path) -> dict:
    """定位、校验并注入 LGPL 全文与其它许可证/通告到 output_dir。

    fail closed：任一 distribution 都找不到有效 LGPL → 抛 LicenseError。
    返回一个可打印的报告 dict。
    """
    if not views:
        raise LicenseError(
            "未发现任何 PySide6/shiboken6 distribution：无法定位 LGPL 文本"
        )

    chosen: Optional[LicenseHit] = None
    extras: list[LicenseHit] = []
    scanned: list[str] = []
    for view in views:
        scanned.append(view.name)
        lgpl_hit, all_hits = scan_distribution(view)
        for h in all_hits:
            if not h.is_lgpl:
                extras.append(h)
        if lgpl_hit is not None and chosen is None:
            chosen = lgpl_hit

    if chosen is None:
        raise LicenseError(
            "在以下 distribution 中均未找到内容有效的 LGPL 全文："
            + ", ".join(scanned)
            + "（不联网下载、不手写，按 fail-closed 退出）"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    # 主 LGPL 全文
    lgpl_target = output_dir / LGPL_OUTPUT_NAME
    copy_verified(chosen.text, chosen.source, lgpl_target)

    # 其它需保留的 Qt/PySide6 许可证 / 通告
    injected_extras: list[str] = []
    written: set[str] = {LGPL_OUTPUT_NAME}
    for h in extras:
        name = _safe_extra_name(h.dist_name, h.source)
        if name in written:
            continue
        try:
            copy_verified(h.text, h.source, output_dir / name)
        except LicenseError:
            # 附属通告复制失败不应静默：主 LGPL 已保证，附属失败照样 fail closed。
            raise
        written.add(name)
        injected_extras.append(name)

    return {
        "scanned_distributions": scanned,
        "lgpl_source_distribution": chosen.dist_name,
        "lgpl_source_file": str(chosen.source),
        "lgpl_target": str(lgpl_target),
        "extra_targets": injected_extras,
    }


# ---------------------------------------------------------------- vendored fallback
def load_vendored_license(licenses_dir: Path, license_id: str) -> tuple[str, Path, dict]:
    """从 packaging/licenses/ 加载经 manifest 校验的官方许可证副本。

    校验（任一不满足 → LicenseError，fail closed）：
    - manifest 存在、合法 JSON、含该 license_id 条目、必需字段齐全
    - manifest 任何字符串值不含占位符
    - 文件存在且非空；SHA-256 与 manifest 一致
    - 内容含正确的许可证标题与 "Version 3, 29 June 2007" 版本行
    - 内容不含占位文本、不含本机个人绝对路径
    绝不联网下载。
    """
    import hashlib
    import json

    manifest_path = licenses_dir / VENDORED_MANIFEST_NAME
    if not manifest_path.is_file():
        raise LicenseError(f"vendored 许可证 manifest 缺失：{manifest_path.name}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LicenseError(f"vendored manifest 无法解析：{exc}") from exc

    entries = manifest.get("licenses")
    if not isinstance(entries, list):
        raise LicenseError("vendored manifest 缺少 licenses 列表")
    entry = next(
        (e for e in entries
         if isinstance(e, dict) and e.get("license_id") == license_id),
        None,
    )
    if entry is None:
        raise LicenseError(f"vendored manifest 中没有 {license_id} 条目")
    for key in REQUIRED_MANIFEST_KEYS:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise LicenseError(f"vendored manifest {license_id}.{key} 缺失或为空")
        if PLACEHOLDER_RE.search(value):
            raise LicenseError(
                f"vendored manifest {license_id}.{key} 仍是占位值：{value[:50]}"
            )
    if not re.fullmatch(r"[0-9a-fA-F]{64}", entry["sha256"]):
        raise LicenseError(f"vendored manifest {license_id}.sha256 不是合法 SHA-256")

    file_path = licenses_dir / entry["file"]
    if not file_path.is_file() or file_path.stat().st_size == 0:
        raise LicenseError(f"vendored 许可证文件缺失或为空：{entry['file']}")
    data = file_path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != entry["sha256"].lower():
        raise LicenseError(
            f"vendored {entry['file']} SHA-256 与 manifest 不一致"
            f"（manifest={entry['sha256'][:12]}… 实际={actual[:12]}…）"
        )
    text = data.decode("utf-8", errors="replace")
    if PLACEHOLDER_RE.search(text):
        raise LicenseError(f"vendored {entry['file']} 内容含占位文本")
    # 个人绝对路径前缀（拼接构造，避免本模块源码自身含该字面量）
    personal_prefix = "/" + "Users" + "/"
    if personal_prefix in text:
        raise LicenseError(f"vendored {entry['file']} 内容含本机个人绝对路径")
    if license_id == "LGPL-3.0-only":
        if not content_is_valid_lgpl3_strict(text):
            raise LicenseError(
                f"vendored {entry['file']} 不是有效的 LGPLv3 全文"
                "（缺少标题或 Version 3, 29 June 2007）"
            )
    elif license_id == "GPL-3.0-only":
        if not content_is_valid_gpl3(text):
            raise LicenseError(
                f"vendored {entry['file']} 不是有效的 GPLv3 全文"
                "（缺少标题或 Version 3, 29 June 2007）"
            )
    else:
        raise LicenseError(f"不支持的 license_id：{license_id}")
    return text, file_path, entry


# ---------------------------------------------------------------- 综合编排（A→B→C）
def _default_pyside6_version() -> str:
    import importlib.metadata as im

    for name in DIST_NAMES:
        try:
            return f"{name} {im.version(name)}"
        except im.PackageNotFoundError:
            continue
    return "unknown (no PySide6 distribution detected)"


def inject_all_licenses(
    views: Sequence[DistributionView],
    output_dir: Path,
    licenses_dir: Optional[Path] = None,
    version_provider=None,
) -> dict:
    """注入 LGPL-3.0.txt、GPL-3.0.txt、PYSIDE6-NOTICE.txt 与校验记录。

    每个许可证的来源顺序（用户裁决）：
    A. 当前安装 distribution 中内容有效的许可证（优先）
    B. packaging/licenses/ 中经 manifest SHA-256 校验的官方副本
    C. 两者均不可用 → LicenseError（fail closed）
    """
    import hashlib

    version_provider = version_provider or _default_pyside6_version

    # --- 扫描 distribution（来源 A），并保留附属通告 ---
    lgpl_hit: Optional[LicenseHit] = None
    gpl_hit: Optional[LicenseHit] = None
    extras: list[LicenseHit] = []
    scanned: list[str] = []
    for view in views:
        scanned.append(view.name)
        dist_lgpl, all_hits = scan_distribution(view)
        if dist_lgpl is not None and lgpl_hit is None:
            lgpl_hit = dist_lgpl
        for hit in all_hits:
            if hit.is_lgpl:
                continue
            if gpl_hit is None and content_is_valid_gpl3(hit.text):
                gpl_hit = hit
            else:
                extras.append(hit)

    output_dir.mkdir(parents=True, exist_ok=True)
    sources: dict[str, str] = {}
    errors: list[str] = []

    def resolve(license_id: str, output_name: str,
                dist_hit: Optional[LicenseHit]) -> Optional[bytes]:
        if dist_hit is not None:
            copy_verified(dist_hit.text, dist_hit.source, output_dir / output_name)
            sources[output_name] = (
                f"installed distribution ({dist_hit.dist_name})"
            )
            return (output_dir / output_name).read_bytes()
        if licenses_dir is not None:
            try:
                text, file_path, _entry = load_vendored_license(
                    licenses_dir, license_id
                )
                copy_verified(text, file_path, output_dir / output_name)
                sources[output_name] = (
                    "audited vendored fallback (packaging/licenses)"
                )
                return (output_dir / output_name).read_bytes()
            except LicenseError as exc:
                errors.append(str(exc))
                return None
        errors.append(
            f"{license_id}：distribution 未提供且未配置 vendored fallback"
        )
        return None

    lgpl_bytes = resolve("LGPL-3.0-only", LGPL_OUTPUT_NAME, lgpl_hit)
    gpl_bytes = resolve("GPL-3.0-only", GPL_OUTPUT_NAME, gpl_hit)
    if lgpl_bytes is None or gpl_bytes is None:
        raise LicenseError(
            "许可证注入失败（fail closed）：\n  - " + "\n  - ".join(errors)
        )

    # --- 附属通告（distribution 中的其它许可证/声明） ---
    injected_extras: list[str] = []
    written: set[str] = {LGPL_OUTPUT_NAME, GPL_OUTPUT_NAME}
    for hit in extras:
        name = _safe_extra_name(hit.dist_name, hit.source)
        if name in written:
            continue
        copy_verified(hit.text, hit.source, output_dir / name)
        written.add(name)
        injected_extras.append(name)

    # --- 构建时 SHA 记录（verify_bundle 据此复核一致性） ---
    record_lines = [
        f"{hashlib.sha256(lgpl_bytes).hexdigest()}  {LGPL_OUTPUT_NAME}",
        f"{hashlib.sha256(gpl_bytes).hexdigest()}  {GPL_OUTPUT_NAME}",
    ]
    (output_dir / RECORD_OUTPUT_NAME).write_text(
        "\n".join(record_lines) + "\n", encoding="utf-8"
    )

    # --- PYSIDE6-NOTICE（不含任何本机绝对路径） ---
    notice = "\n".join([
        "PySide6 / Qt Licence Notice",
        "===========================",
        "",
        f"PySide6 version: {version_provider()}",
        "Licence election: Qt and PySide6 are used under LGPL-3.0-only.",
        "GPL-3.0 full text is included because LGPLv3 incorporates the",
        "terms of GPLv3 by reference.",
        "",
        "Projects and source code:",
        "- Qt:      https://www.qt.io / https://download.qt.io/official_releases/",
        "- PySide6: https://code.qt.io/cgit/pyside/pyside-setup.git/",
        "Corresponding source versions match the PySide6 version above",
        "(see build log pip freeze).",
        "",
        "Licence text provenance for this build:",
        f"- {LGPL_OUTPUT_NAME}: {sources[LGPL_OUTPUT_NAME]}",
        f"- {GPL_OUTPUT_NAME}: {sources[GPL_OUTPUT_NAME]}",
        "",
        "Qt Frameworks are kept as separate, replaceable frameworks in",
        "Contents/Frameworks/ as required by LGPLv3.",
        "",
    ])
    (output_dir / NOTICE_OUTPUT_NAME).write_text(notice, encoding="utf-8")

    return {
        "scanned_distributions": scanned,
        "sources": sources,
        "extra_targets": injected_extras,
        "record": str(RECORD_OUTPUT_NAME),
        "notice": str(NOTICE_OUTPUT_NAME),
    }


# ---------------------------------------------------------------- CLI
def _print_report(report: dict) -> None:
    print(f"[find-qt-licenses] 扫描 distribution：{', '.join(report['scanned_distributions'])}")
    print(f"[find-qt-licenses] LGPL 来源 distribution：{report['lgpl_source_distribution']}")
    print(f"[find-qt-licenses] LGPL 来源文件：{report['lgpl_source_file']}")
    print(f"[find-qt-licenses] 注入 LGPL 到：{report['lgpl_target']}")
    if report["extra_targets"]:
        print(f"[find-qt-licenses] 同时保留其它许可证/通告：{', '.join(report['extra_targets'])}")
    else:
        print("[find-qt-licenses] 未发现额外的 Qt/PySide6 许可证/通告文件")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="定位并校验 Qt/PySide6 许可证全文（LGPL+GPL），"
                    "注入 App licenses 目录（distribution 优先 + "
                    "audited vendored fallback，fail closed）"
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="注入目标目录（通常为 App .../Contents/Resources/licenses）",
    )
    parser.add_argument(
        "--licenses-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "licenses",
        help="仓库 vendored 许可证目录（默认 packaging/licenses）",
    )
    args = parser.parse_args(argv)

    try:
        views = load_installed_views()
        report = inject_all_licenses(
            views, args.output_dir, licenses_dir=args.licenses_dir
        )
    except LicenseError as exc:
        print(f"[find-qt-licenses] 失败（fail closed）：{exc}", file=sys.stderr)
        return 1
    print(f"[find-qt-licenses] 扫描 distribution："
          f"{', '.join(report['scanned_distributions']) or '（无）'}")
    for name, source in report["sources"].items():
        print(f"[find-qt-licenses] {name} ← {source}")
    if report["extra_targets"]:
        print(f"[find-qt-licenses] 附属通告：{', '.join(report['extra_targets'])}")
    print(f"[find-qt-licenses] 已写入 {report['notice']} 与 {report['record']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
