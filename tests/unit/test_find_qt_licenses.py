"""packaging/find_qt_licenses.py 专项测试。

覆盖场景（均不依赖真实 PySide6 安装、不访问网络、不硬编码个人路径）：
- licence 位于 PySide6.dist-info/licenses/
- licence 位于 PySide6_Essentials.dist-info/licenses/
- licence 位于包目录 LICENSES/
- 第一个候选无效、第二个候选有效
- 文件名像 LGPL 但内容错误时拒绝
- 全部缺失时 fail closed
- 空文件被拒绝
- 注入后目标文件存在、非空、内容一致（verify_bundle 语义）
- 其它 Qt 许可证/通告一并保留
- 版本不匹配（LGPL-2.1 声明 vs 正文 v3）时拒绝
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "packaging" / "find_qt_licenses.py"

# 以文件路径直接加载（packaging 不是可导入包）。
_spec = importlib.util.spec_from_file_location("find_qt_licenses", MODULE_PATH)
assert _spec and _spec.loader
fql = importlib.util.module_from_spec(_spec)
sys.modules["find_qt_licenses"] = fql
_spec.loader.exec_module(fql)


# 真实 LGPLv3 正文的最小合规替身：含标题 + 版本行 + 少量正文。
LGPL_V3_TEXT = (
    "GNU LESSER GENERAL PUBLIC LICENSE\n"
    "                       Version 3, 29 June 2007\n\n"
    " Copyright (C) 2007 Free Software Foundation, Inc.\n"
    " This version of the GNU Lesser General Public License incorporates\n"
    " the terms and conditions of version 3 of the GNU General Public License.\n"
)
LGPL_V21_TEXT = (
    "GNU LESSER GENERAL PUBLIC LICENSE\n"
    "                       Version 2.1, February 1999\n\n"
    " Copyright (C) 1991, 1999 Free Software Foundation, Inc.\n"
)
GPL_V3_TEXT = (
    "GNU GENERAL PUBLIC LICENSE\n"
    "                       Version 3, 29 June 2007\n"
)
PYSIDE_LICENSE = "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"


def make_view(name, *, declared=PYSIDE_LICENSE, dist_info=None,
              package_dirs=None, recorded=None):
    return fql.DistributionView(
        name=name,
        declared_license=declared,
        package_dirs=list(package_dirs or []),
        dist_info_dir=dist_info,
        recorded_files=list(recorded or []),
    )


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- 内容校验单元
class TestContentValidation:
    def test_valid_lgpl_v3_accepted(self):
        assert fql.content_is_valid_lgpl(LGPL_V3_TEXT, {"3.0"})

    def test_valid_lgpl_v3_accepted_without_version_constraint(self):
        assert fql.content_is_valid_lgpl(LGPL_V3_TEXT, set())

    def test_empty_text_rejected(self):
        assert not fql.content_is_valid_lgpl("", {"3.0"})
        assert not fql.content_is_valid_lgpl("   \n  ", {"3.0"})

    def test_non_lgpl_rejected(self):
        assert not fql.content_is_valid_lgpl(GPL_V3_TEXT, set())

    def test_wrong_version_rejected(self):
        # 声明 LGPL-2.1，但正文是 v3 → 拒绝
        assert not fql.content_is_valid_lgpl(LGPL_V3_TEXT, {"2.1"})

    def test_expected_versions_parsing(self):
        assert fql.expected_lgpl_versions(PYSIDE_LICENSE) == {"3.0"}
        assert fql.expected_lgpl_versions("LGPL-2.1-or-later") == {"2.1"}
        assert fql.expected_lgpl_versions("MIT") == set()


# ---------------------------------------------------------------- 位置发现
class TestLocationDiscovery:
    def test_found_in_pyside6_dist_info_licenses(self, tmp_path):
        di = tmp_path / "PySide6-6.11.1.dist-info"
        write(di / "licenses" / "LICENSE.LGPLv3", LGPL_V3_TEXT)
        view = make_view("PySide6", dist_info=di)
        out = tmp_path / "out"
        report = fql.inject_licenses([view], out)
        assert report["lgpl_source_distribution"] == "PySide6"
        target = out / "LGPL-3.0.txt"
        assert target.read_text(encoding="utf-8") == LGPL_V3_TEXT

    def test_found_in_essentials_dist_info_licenses(self, tmp_path):
        # PySide6 主包无许可证，Essentials 携带 → 应回退到 Essentials
        empty_di = tmp_path / "PySide6-6.11.1.dist-info"
        empty_di.mkdir(parents=True)
        ess = tmp_path / "PySide6_Essentials-6.11.1.dist-info"
        write(ess / "licenses" / "LGPL_EXCEPTION.txt", LGPL_V3_TEXT)
        views = [make_view("PySide6", dist_info=empty_di),
                 make_view("PySide6_Essentials", dist_info=ess)]
        out = tmp_path / "out"
        report = fql.inject_licenses(views, out)
        assert report["lgpl_source_distribution"] == "PySide6_Essentials"

    def test_found_in_package_licenses_dir(self, tmp_path):
        pkg = tmp_path / "PySide6"
        write(pkg / "LICENSES" / "LGPL-3.0.txt", LGPL_V3_TEXT)
        view = make_view("PySide6", package_dirs=[pkg])
        out = tmp_path / "out"
        report = fql.inject_licenses([view], out)
        assert report["lgpl_source_file"].endswith("LGPL-3.0.txt")
        assert (out / "LGPL-3.0.txt").read_text(encoding="utf-8") == LGPL_V3_TEXT

    def test_found_via_recorded_files(self, tmp_path):
        f = write(tmp_path / "elsewhere" / "COPYING.LESSER", LGPL_V3_TEXT)
        view = make_view("shiboken6", recorded=[f])
        out = tmp_path / "out"
        report = fql.inject_licenses([view], out)
        assert report["lgpl_source_distribution"] == "shiboken6"


# ---------------------------------------------------------------- 候选优先与拒绝
class TestCandidateSelection:
    def test_first_invalid_second_valid(self, tmp_path):
        # dist-info/licenses/ 下第一个候选内容错误（GPL），第二个才是 LGPL
        di = tmp_path / "PySide6-6.11.1.dist-info"
        write(di / "licenses" / "AAA_LICENSE.txt", GPL_V3_TEXT)   # 名字像但内容非 LGPL
        write(di / "licenses" / "ZZZ_LGPL.txt", LGPL_V3_TEXT)     # 有效
        view = make_view("PySide6", dist_info=di)
        out = tmp_path / "out"
        report = fql.inject_licenses([view], out)
        assert report["lgpl_source_file"].endswith("ZZZ_LGPL.txt")
        # 非 LGPL 的 GPL 文本作为“其它许可证/通告”保留
        assert any("AAA_LICENSE" in e for e in report["extra_targets"])

    def test_name_like_lgpl_but_wrong_content_rejected(self, tmp_path):
        di = tmp_path / "PySide6-6.11.1.dist-info"
        write(di / "licenses" / "LGPL-3.0.txt", "this file is named LGPL but is not the license\n")
        view = make_view("PySide6", dist_info=di)
        out = tmp_path / "out"
        with pytest.raises(fql.LicenseError):
            fql.inject_licenses([view], out)

    def test_empty_file_rejected(self, tmp_path):
        di = tmp_path / "PySide6-6.11.1.dist-info"
        write(di / "licenses" / "LGPL-3.0.txt", "")
        view = make_view("PySide6", dist_info=di)
        with pytest.raises(fql.LicenseError):
            fql.inject_licenses([view], tmp_path / "out")

    def test_version_mismatch_rejected(self, tmp_path):
        # 声明 LGPL-2.1，仅提供 v3 正文 → 无有效命中 → fail closed
        di = tmp_path / "PySide6-6.11.1.dist-info"
        write(di / "licenses" / "LGPL.txt", LGPL_V3_TEXT)
        view = make_view("PySide6", declared="LGPL-2.1-only", dist_info=di)
        with pytest.raises(fql.LicenseError):
            fql.inject_licenses([view], tmp_path / "out")


# ---------------------------------------------------------------- fail closed
class TestFailClosed:
    def test_all_missing_fails(self, tmp_path):
        empty = tmp_path / "PySide6-6.11.1.dist-info"
        empty.mkdir(parents=True)
        view = make_view("PySide6", dist_info=empty)
        with pytest.raises(fql.LicenseError):
            fql.inject_licenses([view], tmp_path / "out")

    def test_no_distributions_fails(self, tmp_path):
        with pytest.raises(fql.LicenseError):
            fql.inject_licenses([], tmp_path / "out")

    def test_cli_returns_nonzero_when_missing(self, tmp_path, monkeypatch):
        """完全隔离环境：无 distribution + 无效 vendored 目录 → 非零退出。

        必须显式把 --licenses-dir 指向 tmp 下不存在的目录：
        仓库 packaging/licenses 现已含合法的 audited vendored 副本，
        默认目录会（正确地）兜底成功。
        """
        monkeypatch.setattr(fql, "load_installed_views", lambda names=fql.DIST_NAMES: [])
        rc = fql.main([
            "--output-dir", str(tmp_path / "out"),
            "--licenses-dir", str(tmp_path / "no_such_vendored_dir"),
        ])
        assert rc == 1
        assert not (tmp_path / "out" / "LGPL-3.0.txt").exists()

    def test_cli_succeeds_with_repo_vendored_fallback(self, tmp_path, monkeypatch):
        """无 distribution + 仓库合法 vendored 副本 → CLI 返回 0 并注入四产物。"""
        repo_licenses = ROOT / "packaging" / "licenses"
        if not (repo_licenses / "LGPL-3.0.txt").is_file():
            pytest.fail("仓库 vendored 许可证副本缺失（应已按构建流程就位）")
        monkeypatch.setattr(fql, "load_installed_views", lambda names=fql.DIST_NAMES: [])
        out = tmp_path / "out"
        rc = fql.main([
            "--output-dir", str(out),
            "--licenses-dir", str(repo_licenses),
        ])
        assert rc == 0
        for name in ("LGPL-3.0.txt", "GPL-3.0.txt", "PYSIDE6-NOTICE.txt",
                     "licenses_record.sha256"):
            assert (out / name).is_file() and (out / name).stat().st_size > 0


# ---------------------------------------------------------------- 注入一致性 & 附属
class TestInjectionIntegrity:
    def test_injected_matches_source_and_nonempty(self, tmp_path):
        di = tmp_path / "PySide6-6.11.1.dist-info"
        src = write(di / "licenses" / "LICENSE.LGPLv3", LGPL_V3_TEXT)
        view = make_view("PySide6", dist_info=di)
        out = tmp_path / "out"
        fql.inject_licenses([view], out)
        target = out / "LGPL-3.0.txt"
        assert target.exists()
        assert target.stat().st_size > 0
        assert target.read_bytes() == src.read_bytes()

    def test_copy_verified_detects_empty_source(self, tmp_path):
        src = write(tmp_path / "empty.txt", "")
        with pytest.raises(fql.LicenseError):
            fql.copy_verified("", src, tmp_path / "out" / "x.txt")

    def test_other_qt_notices_preserved(self, tmp_path):
        di = tmp_path / "PySide6-6.11.1.dist-info"
        write(di / "licenses" / "LICENSE.LGPLv3", LGPL_V3_TEXT)
        write(di / "licenses" / "LICENSE.GPLv3", GPL_V3_TEXT)
        view = make_view("PySide6", dist_info=di)
        out = tmp_path / "out"
        report = fql.inject_licenses([view], out)
        # GPL 全文作为附属被保留（文件真实存在、非空）
        assert report["extra_targets"], "应保留其它 Qt/PySide6 许可证/通告"
        for name in report["extra_targets"]:
            assert (out / name).stat().st_size > 0


# ---------------------------------------------------------------- 不含个人路径 / 不联网
class TestNoNetworkNoPersonalPath:
    def test_module_source_has_no_personal_path(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert "视频剪辑自动化" not in text

    def test_module_does_not_import_network_libs(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        for banned in ("urllib.request", "urllib.error", "requests",
                       "httpx", "socket", "http.client", "ftplib"):
            assert banned not in text, f"许可证定位不得联网：{banned}"
