"""find_qt_licenses 的 vendored fallback 与综合编排测试。

覆盖（不联网、不安装/修改 PySide6、不依赖真实 PySide6 distribution）：
- distribution 自带有效文本时优先使用
- distribution 缺失时使用经 manifest 校验的 vendored fallback
- vendored SHA 不符 / LGPL 正文错误 / GPL 正文错误 / manifest 占位符 → 失败
- 两种来源都不可用 → 失败
- 注入产物：LGPL、GPL、PYSIDE6-NOTICE、licenses_record.sha256
- NOTICE 记录版本与来源，且不含本机绝对路径
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "packaging" / "find_qt_licenses.py"

_spec = importlib.util.spec_from_file_location("find_qt_licenses_fb", MODULE_PATH)
assert _spec and _spec.loader
fql = importlib.util.module_from_spec(_spec)
sys.modules["find_qt_licenses_fb"] = fql
_spec.loader.exec_module(fql)

LGPL_TEXT = (
    "GNU LESSER GENERAL PUBLIC LICENSE\n"
    "                       Version 3, 29 June 2007\n\n"
    " This version of the GNU Lesser General Public License incorporates\n"
    " the terms and conditions of version 3 of the GNU General Public License.\n"
)
GPL_TEXT = (
    "GNU GENERAL PUBLIC LICENSE\n"
    "                       Version 3, 29 June 2007\n\n"
    " Everyone is permitted to copy and distribute verbatim copies.\n"
)
PYSIDE_LICENSE = "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_vendored(tmp_path: Path, lgpl: str = LGPL_TEXT, gpl: str = GPL_TEXT,
                  lgpl_sha: str | None = None, gpl_sha: str | None = None,
                  placeholder: bool = False) -> Path:
    licenses_dir = tmp_path / "licenses"
    write(licenses_dir / "LGPL-3.0.txt", lgpl)
    write(licenses_dir / "GPL-3.0.txt", gpl)
    manifest = {
        "licenses": [
            {
                "file": "LGPL-3.0.txt",
                "license_id": "LGPL-3.0-only",
                "source_url": "https://www.gnu.org/licenses/lgpl-3.0.txt",
                "sha256": lgpl_sha or sha(lgpl),
                "retrieved": "2026-07-25",
                "provenance": (
                    "FILL_ME_placeholder" if placeholder
                    else "downloaded manually from gnu.org by maintainer"
                ),
            },
            {
                "file": "GPL-3.0.txt",
                "license_id": "GPL-3.0-only",
                "source_url": "https://www.gnu.org/licenses/gpl-3.0.txt",
                "sha256": gpl_sha or sha(gpl),
                "retrieved": "2026-07-25",
                "provenance": "downloaded manually from gnu.org by maintainer",
            },
        ]
    }
    write(licenses_dir / "license_manifest.json",
          json.dumps(manifest, ensure_ascii=False))
    return licenses_dir


def dist_view_with_texts(tmp_path: Path) -> "fql.DistributionView":
    di = tmp_path / "PySide6-6.11.1.dist-info"
    write(di / "licenses" / "LICENSE.LGPLv3", LGPL_TEXT)
    write(di / "licenses" / "LICENSE.GPLv3", GPL_TEXT)
    return fql.DistributionView(
        name="PySide6", declared_license=PYSIDE_LICENSE,
        package_dirs=[], dist_info_dir=di, recorded_files=[],
    )


class TestSourcePriority:
    def test_distribution_preferred_over_vendored(self, tmp_path) -> None:
        """A 优先：distribution 有效文本存在时不使用 vendored。"""
        vendored = make_vendored(tmp_path, lgpl="GNU LESSER GENERAL PUBLIC LICENSE\n"
                                                "Version 3, 29 June 2007\nVENDORED-MARK\n")
        out = tmp_path / "out"
        report = fql.inject_all_licenses(
            [dist_view_with_texts(tmp_path)], out, licenses_dir=vendored,
            version_provider=lambda: "PySide6 6.11.1",
        )
        assert report["sources"]["LGPL-3.0.txt"].startswith("installed distribution")
        assert report["sources"]["GPL-3.0.txt"].startswith("installed distribution")
        assert "VENDORED-MARK" not in (out / "LGPL-3.0.txt").read_text("utf-8")

    def test_vendored_fallback_when_distribution_missing(self, tmp_path) -> None:
        """B：distribution 缺失 → 使用经校验的 vendored 副本。"""
        vendored = make_vendored(tmp_path)
        out = tmp_path / "out"
        report = fql.inject_all_licenses(
            [], out, licenses_dir=vendored,
            version_provider=lambda: "PySide6 6.11.1",
        )
        assert report["sources"]["LGPL-3.0.txt"] == \
            "audited vendored fallback (packaging/licenses)"
        assert (out / "LGPL-3.0.txt").read_text("utf-8") == LGPL_TEXT
        assert (out / "GPL-3.0.txt").read_text("utf-8") == GPL_TEXT

    def test_partial_distribution_mixes_sources(self, tmp_path) -> None:
        """dist 只有 LGPL 时：LGPL 用 dist，GPL 走 vendored。"""
        di = tmp_path / "PySide6-6.11.1.dist-info"
        write(di / "licenses" / "LICENSE.LGPLv3", LGPL_TEXT)
        view = fql.DistributionView(
            name="PySide6", declared_license=PYSIDE_LICENSE,
            package_dirs=[], dist_info_dir=di, recorded_files=[],
        )
        vendored = make_vendored(tmp_path)
        out = tmp_path / "out"
        report = fql.inject_all_licenses(
            [view], out, licenses_dir=vendored,
            version_provider=lambda: "PySide6 6.11.1",
        )
        assert report["sources"]["LGPL-3.0.txt"].startswith("installed distribution")
        assert report["sources"]["GPL-3.0.txt"].startswith("audited vendored")


class TestVendoredFailClosed:
    def run_fallback(self, licenses_dir, tmp_path):
        return fql.inject_all_licenses(
            [], tmp_path / "out", licenses_dir=licenses_dir,
            version_provider=lambda: "test",
        )

    def test_sha_mismatch_fails(self, tmp_path) -> None:
        vendored = make_vendored(tmp_path, lgpl_sha="a" * 64)
        with pytest.raises(fql.LicenseError, match="SHA-256"):
            self.run_fallback(vendored, tmp_path)

    def test_wrong_lgpl_body_fails(self, tmp_path) -> None:
        vendored = make_vendored(tmp_path, lgpl="not a licence at all\n")
        with pytest.raises(fql.LicenseError, match="LGPLv3"):
            self.run_fallback(vendored, tmp_path)

    def test_wrong_gpl_body_fails(self, tmp_path) -> None:
        # GPL 文件内容误放 LGPL 全文 → 拒绝
        vendored = make_vendored(tmp_path, gpl=LGPL_TEXT)
        with pytest.raises(fql.LicenseError, match="GPLv3"):
            self.run_fallback(vendored, tmp_path)

    def test_manifest_placeholder_fails(self, tmp_path) -> None:
        vendored = make_vendored(tmp_path, placeholder=True)
        with pytest.raises(fql.LicenseError, match="占位"):
            self.run_fallback(vendored, tmp_path)

    def test_missing_manifest_fails(self, tmp_path) -> None:
        licenses_dir = tmp_path / "licenses"
        write(licenses_dir / "LGPL-3.0.txt", LGPL_TEXT)
        with pytest.raises(fql.LicenseError, match="manifest"):
            self.run_fallback(licenses_dir, tmp_path)

    def test_both_sources_unavailable_fails(self, tmp_path) -> None:
        with pytest.raises(fql.LicenseError, match="fail closed"):
            fql.inject_all_licenses([], tmp_path / "out", licenses_dir=None,
                                    version_provider=lambda: "t")

    def test_placeholder_manifest_is_fail_closed(self, tmp_path) -> None:
        """自建含 FILL_ME 的 manifest → 必须 fail closed。

        （不再假设仓库正式 manifest 处于模板状态——它已按构建流程
        填入真实 GNU 官方 SHA-256。）
        """
        licenses_dir = tmp_path / "licenses"
        write(licenses_dir / "LGPL-3.0.txt", LGPL_TEXT)
        write(licenses_dir / "license_manifest.json", json.dumps({
            "licenses": [{
                "file": "LGPL-3.0.txt",
                "license_id": "LGPL-3.0-only",
                "source_url": "https://www.gnu.org/licenses/lgpl-3.0.txt",
                "sha256": "FILL_ME_sha256_of_downloaded_lgpl_text",
                "retrieved": "2026-07-25",
                "provenance": "test",
            }]
        }))
        with pytest.raises(fql.LicenseError, match="占位"):
            fql.load_vendored_license(licenses_dir, "LGPL-3.0-only")
        with pytest.raises(fql.LicenseError):
            fql.inject_all_licenses([], tmp_path / "out",
                                    licenses_dir=licenses_dir,
                                    version_provider=lambda: "t")


class TestRepoManifestValid:
    """仓库正式 vendored 副本的有效性（不联网、只读仓库文件）。"""

    REPO_LICENSES = ROOT / "packaging" / "licenses"

    def test_repo_license_manifest_is_valid(self) -> None:
        manifest_path = self.REPO_LICENSES / "license_manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        # 无占位符
        assert "FILL_ME" not in json.dumps(data)
        entries = {entry["license_id"]: entry for entry in data["licenses"]}
        assert set(entries) == {"LGPL-3.0-only", "GPL-3.0-only"}
        headers = {
            "LGPL-3.0-only": "GNU LESSER GENERAL PUBLIC LICENSE",
            "GPL-3.0-only": "GNU GENERAL PUBLIC LICENSE",
        }
        for license_id, entry in entries.items():
            file_path = self.REPO_LICENSES / entry["file"]
            # 文件存在、SHA 与 manifest 一致
            assert file_path.is_file(), f"缺少 {entry['file']}"
            actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
            assert actual == entry["sha256"].lower(), \
                f"{entry['file']} SHA 与 manifest 不一致"
            # 正文标题与版本正确
            text = file_path.read_text(encoding="utf-8")
            assert headers[license_id] in text
            assert "Version 3, 29 June 2007" in text
            # 加载器对两个 license_id 均成功（完整校验链路）
            loaded_text, loaded_path, loaded_entry = \
                fql.load_vendored_license(self.REPO_LICENSES, license_id)
            assert loaded_path == file_path
            assert loaded_entry["sha256"] == entry["sha256"]


class TestOutputsAndNotice:
    def test_all_four_outputs_written(self, tmp_path) -> None:
        vendored = make_vendored(tmp_path)
        out = tmp_path / "out"
        fql.inject_all_licenses([], out, licenses_dir=vendored,
                                version_provider=lambda: "PySide6 6.11.1")
        for name in ("LGPL-3.0.txt", "GPL-3.0.txt", "PYSIDE6-NOTICE.txt",
                     "licenses_record.sha256"):
            assert (out / name).is_file() and (out / name).stat().st_size > 0

    def test_record_matches_files(self, tmp_path) -> None:
        vendored = make_vendored(tmp_path)
        out = tmp_path / "out"
        fql.inject_all_licenses([], out, licenses_dir=vendored,
                                version_provider=lambda: "t")
        record = (out / "licenses_record.sha256").read_text("utf-8")
        for name in ("LGPL-3.0.txt", "GPL-3.0.txt"):
            actual = hashlib.sha256((out / name).read_bytes()).hexdigest()
            assert f"{actual}  {name}" in record

    def test_notice_content_and_no_personal_paths(self, tmp_path) -> None:
        vendored = make_vendored(tmp_path)
        out = tmp_path / "out"
        fql.inject_all_licenses([], out, licenses_dir=vendored,
                                version_provider=lambda: "PySide6 6.11.1")
        notice = (out / "PYSIDE6-NOTICE.txt").read_text("utf-8")
        assert "PySide6 6.11.1" in notice                    # 实际版本
        assert "LGPL-3.0-only" in notice                     # 许可选择
        assert "audited vendored fallback" in notice         # 来源记录
        assert "code.qt.io" in notice                        # 源码获取
        assert "/Users/" not in notice                       # 无本机绝对路径
        assert str(tmp_path) not in notice

    def test_no_network_imports(self) -> None:
        text = MODULE_PATH.read_text(encoding="utf-8")
        for banned in ("urllib.request", "requests", "httpx", "socket"):
            assert banned not in text
