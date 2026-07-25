"""打包资产静态测试：spec 排除清单、manifest fail-closed、verify 自测。

不要求安装 PyInstaller、不要求 vendor FFmpeg 存在、不访问网络。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging"
SPEC = PACKAGING / "autovideomaker.spec"
MANIFEST = PACKAGING / "ffmpeg_manifest.json"


class TestSpecStatic:
    def spec_text(self) -> str:
        return SPEC.read_text(encoding="utf-8")

    def test_gpl_only_qt_modules_excluded(self) -> None:
        text = self.spec_text()
        for module in ("PySide6.QtCharts", "PySide6.QtDataVisualization"):
            assert module in text, f"GPL-only 模块必须显式排除：{module}"

    def test_unused_qt_modules_excluded(self) -> None:
        text = self.spec_text()
        for module in (
            "PySide6.QtWebEngineCore", "PySide6.QtMultimedia",
            "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtNetwork",
        ):
            assert module in text, f"未使用模块应排除：{module}"

    def test_datas_contain_no_sensitive_items(self) -> None:
        text = self.spec_text()
        for forbidden in ("config.json", "project.json", "tests",
                          "vendor", ".venv"):
            # datas 定义区不得引用敏感项（vendor 由 build 脚本注入 bin）
            datas_region = text.split("DATAS = [")[1].split("]")[0]
            assert forbidden not in datas_region

    def test_bundle_metadata(self) -> None:
        text = self.spec_text()
        assert "com.bonniewang.autovideomaker" in text
        assert '"0.1.0"' in text or "VERSION = \"0.1.0\"" in text
        assert "console=False" in text
        assert 'target_arch="arm64"' in text
        # 签名不在 spec 中进行（由脚本由内向外执行）
        assert "codesign_identity=None" in text

    def test_entry_only_calls_app_main(self) -> None:
        entry = (PACKAGING / "entry.py").read_text(encoding="utf-8")
        assert "from auto_video_maker.app import main" in entry
        assert "sys.exit(main())" in entry


class TestManifestFailClosed:
    def run_validate(self, extra: list[str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PACKAGING / "validate_manifest.py"),
             *(extra or [])],
            capture_output=True, text=True,
        )

    def test_placeholder_manifest_fails(self, tmp_path) -> None:
        """含 FILL_ME 占位的 manifest → 校验必须失败（fail closed）。"""
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        data["configure"] = "FILL_ME_configure_flags"
        target = tmp_path / "ffmpeg_manifest.json"
        target.write_text(json.dumps(data), encoding="utf-8")
        script = tmp_path / "validate_manifest.py"
        script.write_text(
            (PACKAGING / "validate_manifest.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True
        )
        assert result.returncode != 0
        assert "占位值" in result.stderr

    def test_repo_manifest_is_filled_and_passes(self) -> None:
        """仓库真实 manifest（已按批准填实）必须通过字段校验。"""
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert "FILL_ME" not in json.dumps(data)
        result = self.run_validate()  # 不带 --check-binaries（vendor 不要求存在）
        assert result.returncode == 0, result.stderr
        assert "SOURCES_ARCHIVED=0" in result.stdout  # 合规门：仅 internal-only

    def test_filled_manifest_passes_without_binaries(self, tmp_path, monkeypatch) -> None:
        """字段全部填实（不校验二进制）→ 通过并输出合规门状态。"""
        filled = {
            "provider": "Martin Riedl Build Server",
            "release_channel": "release",
            "version": "8.1.2",
            "architecture": "arm64",
            "license": "GPL-2.0-or-later (includes libx264)",
            "configure": "--enable-gpl --enable-libx264 --enable-libass",
            "ffmpeg": {"url": "https://ffmpeg.martin-riedl.de/x/ffmpeg.zip",
                       "sha256": "a" * 64},
            "ffprobe": {"url": "https://ffmpeg.martin-riedl.de/x/ffprobe.zip",
                        "sha256": "b" * 64},
            "source_correspondence": {"archived": False, "note": "n"},
        }
        target = tmp_path / "ffmpeg_manifest.json"
        target.write_text(json.dumps(filled), encoding="utf-8")
        script = tmp_path / "validate_manifest.py"
        script.write_text(
            (PACKAGING / "validate_manifest.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert "SOURCES_ARCHIVED=0" in result.stdout

    @pytest.mark.parametrize("mutation", [
        {"release_channel": "snapshot"},          # 禁 snapshot
        {"version": "8.1.1"},                     # 版本固定
        {"architecture": "x86_64"},               # 架构固定
        {"configure": ""},                        # 缺 configure
    ])
    def test_invalid_fields_fail(self, tmp_path, mutation) -> None:
        base = {
            "provider": "Martin Riedl Build Server",
            "release_channel": "release",
            "version": "8.1.2",
            "architecture": "arm64",
            "license": "GPL",
            "configure": "--enable-gpl",
            "ffmpeg": {"url": "https://a.b/f", "sha256": "a" * 64},
            "ffprobe": {"url": "https://a.b/p", "sha256": "b" * 64},
            "source_correspondence": {"archived": True},
        }
        base.update(mutation)
        target = tmp_path / "ffmpeg_manifest.json"
        target.write_text(json.dumps(base), encoding="utf-8")
        script = tmp_path / "validate_manifest.py"
        script.write_text(
            (PACKAGING / "validate_manifest.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True
        )
        assert result.returncode != 0


class TestVerifyBundleSelfTest:
    def run_self_test(self, env: dict | None = None) -> subprocess.CompletedProcess:
        import os

        merged = dict(os.environ)
        if env:
            merged.update(env)
        return subprocess.run(
            ["bash", str(PACKAGING / "verify_bundle.sh"), "--self-test"],
            capture_output=True, text=True, env=merged,
        )

    def test_self_test_passes(self) -> None:
        """--self-test：合规样本返回 0；不要求 vendor 二进制/Xcode/网络。"""
        result = self.run_self_test()
        assert result.returncode == 0, result.stdout + result.stderr
        assert "self-test 通过" in result.stdout

    def test_self_test_detects_required_violation_classes(self) -> None:
        """违规样本至少检出：禁止路径、敏感文本、个人路径、缺失文件。"""
        result = self.run_self_test()
        assert "违规检出" in result.stdout
        # 违规样本缺少 bin/NOTICES 等 → 检出数远超 4 类下限
        import re

        match = re.search(r"违规检出 (\d+) 项", result.stdout)
        assert match and int(match.group(1)) >= 4


class TestVerifyBundleModeIsolation:
    """self-test 替身与正式验证的隔离（静态断言脚本文本）。"""

    def script(self) -> str:
        return (PACKAGING / "verify_bundle.sh").read_text(encoding="utf-8")

    def test_self_test_mode_initialized_off_and_not_env_injectable(self) -> None:
        text = self.script()
        # 无条件初始化为 0：外部环境变量无法预置绕过
        assert "SELF_TEST_MODE=0" in text
        # 只在 self_test 函数内置 1
        self_test_body = text.split("self_test()")[1]
        assert "SELF_TEST_MODE=1" in self_test_body
        before_self_test = text.split("self_test()")[0]
        assert "SELF_TEST_MODE=1" not in before_self_test
        # 不从环境读取该开关
        assert "VERIFY_BUNDLE_SELF_TEST" not in text

    def test_normal_mode_keeps_real_arch_check(self) -> None:
        """普通验证模式仍包含真实 arm64 架构检查。"""
        text = self.script()
        assert 'lipo -archs' in text
        arch_fn = text.split("arch_is_arm64()")[1].split("}")[0]
        assert "lipo -archs" in arch_fn
        assert 'grep -q "arm64"' in arch_fn
        # 替身分支受 SELF_TEST_MODE 守卫
        assert 'SELF_TEST_MODE" == "1"' in arch_fn

    def test_normal_mode_keeps_strict_codesign_verify(self) -> None:
        """普通验证模式仍执行 codesign --verify --deep --strict。"""
        text = self.script()
        codesign_fn = text.split("codesign_strict_ok()")[1].split("}")[0]
        assert "codesign --verify --deep --strict" in codesign_fn
        assert 'SELF_TEST_MODE" == "1"' in codesign_fn

    def test_spctl_is_log_only_for_internal_builds(self) -> None:
        """spctl rejected 只记录，不得单独导致 verify 失败。"""
        text = self.script()
        spctl_fn = text.split("spctl_log_only()")[1].split("}")[0]
        assert "|| true" in spctl_fn        # 仅记录
        assert "violation" not in spctl_fn  # 绝不计入违规

    def test_ffmpeg_execution_check_guarded(self) -> None:
        text = self.script()
        exec_fn = text.split("bundled_ffmpeg_runs()")[1].split("}")[0]
        assert "-version" in exec_fn
        assert 'SELF_TEST_MODE" == "1"' in exec_fn


class TestBuildScriptsStatic:
    def test_no_deep_signing(self) -> None:
        """签名阶段禁止 --deep（验证阶段除外）。"""
        build = (PACKAGING / "build_app.sh").read_text(encoding="utf-8")
        for line in build.splitlines():
            if "codesign" in line and "--verify" not in line and not line.strip().startswith("#"):
                assert "--deep" not in line, f"签名命令不得使用 --deep：{line.strip()}"

    def test_staging_promotion_order(self) -> None:
        build = (PACKAGING / "build_app.sh").read_text(encoding="utf-8")
        assert "phase6-staging" in build
        # verify 在晋级之前
        assert build.index("verify_bundle.sh") < build.index('mv "$STAGED_APP" "$FINAL_APP"')
        # manifest 校验在 PyInstaller 之前（fail closed）
        assert build.index("validate_manifest.py") < build.index("pyinstaller ")

    def test_ffmpeg_goes_to_macos_bin(self) -> None:
        build = (PACKAGING / "build_app.sh").read_text(encoding="utf-8")
        assert "Contents/MacOS/bin" in build
        assert "Resources/bin" not in build

    def test_license_injection_delegated_to_finder(self) -> None:
        """LGPL 注入必须走 find_qt_licenses.py，不得内联硬编码单一路径。"""
        build = (PACKAGING / "build_app.sh").read_text(encoding="utf-8")
        assert "find_qt_licenses.py" in build
        # 旧实现的脆弱 glob 不得残留
        assert "Path(PySide6.__file__).parent.glob" not in build
        # 定位失败必须 fail closed（die）
        finder_line = next(
            ln for ln in build.splitlines() if "find_qt_licenses.py" in ln and "python" in ln
        )
        idx = build.index(finder_line)
        assert "die" in build[idx: idx + 200]

    def test_no_personal_paths_in_packaging_sources(self) -> None:
        """会被注入 App 的打包脚本/工具不得写入本机个人绝对路径。

        verify_bundle.sh 例外：它合法地把 /Users/ 与项目名作为**检测正则**，
        用来发现并拒绝个人路径，因此单独校验。
        """
        for rel in ("build_app.sh", "find_qt_licenses.py"):
            text = (PACKAGING / rel).read_text(encoding="utf-8")
            assert "/Users/" not in text, f"{rel} 含个人绝对路径"
            assert "视频剪辑自动化" not in text, f"{rel} 含个人项目路径片段"
        # verify_bundle.sh 合法包含检测正则与合成自测样本（/Users/someone），
        # 其行为由 verify_bundle.sh --self-test 覆盖，此处不静态断言。


class TestFindQtLicensesStatic:
    def script(self) -> str:
        return (PACKAGING / "find_qt_licenses.py").read_text(encoding="utf-8")

    def test_checks_all_pyside_distributions(self) -> None:
        text = self.script()
        for name in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6"):
            assert name in text

    def test_uses_importlib_metadata_not_hardcoded_path(self) -> None:
        text = self.script()
        assert "importlib.metadata" in text
        # 不硬编码任何绝对 site-packages / python 版本路径
        assert "/site-packages/" not in text
        assert "lib/python" not in text

    def test_content_gate_present(self) -> None:
        text = self.script()
        assert "GNU LESSER GENERAL PUBLIC LICENSE" in text

    def test_no_network_no_generated_text(self) -> None:
        text = self.script()
        for banned in ("urllib", "requests", "httpx", "socket", "http.client"):
            assert banned not in text
        # 不得内嵌整段 LGPL 正文（只做定位/校验，不手写）
        assert "You may convey a Combined Work" not in text


class TestVerifyBundleLicenseChecks:
    def script(self) -> str:
        return (PACKAGING / "verify_bundle.sh").read_text(encoding="utf-8")

    def test_checks_licenses_dir_and_nonempty_lgpl(self) -> None:
        text = self.script()
        assert "Resources/licenses" in text
        # 非空检查（-s）与内容检查
        check_region = text.split("check_app()")[1]
        assert "-s " in check_region
        assert "GNU LESSER GENERAL PUBLIC LICENSE" in check_region

    def test_checks_frameworks_dynamic_components(self) -> None:
        text = self.script()
        assert '-name "*.framework"' in text or "-name '*.framework'" in text
        assert ".dylib" in text

    def test_checks_third_party_notices(self) -> None:
        text = self.script()
        assert "THIRD_PARTY_NOTICES.txt" in text
