# -*- mode: python ; coding: utf-8 -*-
"""Auto Video Maker 打包配置（PyInstaller spec）。

- 仅 macOS arm64；windowed .app
- FFmpeg/ffprobe 不经 spec 打入：由 build_app.sh 在打包后注入
  Contents/MacOS/bin/ 并按由内向外顺序单独签名（TASK.md 裁决）
- 排除项均有注释；GPL-only 的 Qt 模块必须排除
"""

from pathlib import Path

SPEC_DIR = Path(SPECPATH)
REPO_ROOT = SPEC_DIR.parent

APP_NAME = "Auto Video Maker"
BUNDLE_ID = "com.bonniewang.autovideomaker"
VERSION = "0.1.0"

# 显式排除（逐项注释；对应 tests/unit/test_packaging_spec.py 静态断言）
EXCLUDES = [
    # --- Qt：未使用的大型模块 ---
    "PySide6.QtWebEngineCore",      # 未使用；体积巨大
    "PySide6.QtWebEngineWidgets",   # 未使用
    "PySide6.QtMultimedia",         # 未使用（音频经 FFmpeg/系统播放器）
    "PySide6.QtMultimediaWidgets",  # 未使用
    "PySide6.Qt3DCore",             # 未使用
    "PySide6.Qt3DRender",           # 未使用
    "PySide6.QtQuick",              # 未使用（纯 QtWidgets 应用）
    "PySide6.QtQml",                # 未使用
    "PySide6.QtNetwork",            # 未使用（网络经 httpx/aiohttp）
    # --- Qt：GPL-only 模块（LGPL 合规裁决，必须排除）---
    "PySide6.QtCharts",             # GPL-only
    "PySide6.QtDataVisualization",  # GPL-only
    # --- 标准库/生态：未使用 ---
    "tkinter",                      # 未使用
    "unittest",                     # 测试框架不入包
    "pytest",                       # 测试框架不入包
]

# datas：仅图标与第三方声明；严禁 config/项目数据/素材/tests/vendor
DATAS = [
    (str(SPEC_DIR / "icon.icns"), "."),
    (str(SPEC_DIR / "THIRD_PARTY_NOTICES.txt"), "."),
]

HIDDEN_IMPORTS = [
    "edge_tts",
    "mutagen.mp3",
    "aiohttp",
    "certifi",
]

a = Analysis(
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    disable_windowed_traceback=False,
    target_arch="arm64",
    codesign_identity=None,   # 签名由 build_app.sh 由内向外执行
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=str(SPEC_DIR / "icon.icns"),
    bundle_identifier=BUNDLE_ID,
    version=VERSION,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.video",
        # LSMinimumSystemVersion 以真实验证结果为准（TASK.md 裁决 5）
        "LSMinimumSystemVersion": "13.0",
    },
)
