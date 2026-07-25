#!/bin/bash
# Auto Video Maker macOS 打包脚本（仅在 macOS arm64 构建机运行）
#
# 用法：
#   ./packaging/build_app.sh [--internal-only]
#
# 关键约束（TASK.md Phase 6）：
# - manifest fail closed：占位/缺失/SHA 不符 → 立即退出，不跑 PyInstaller
# - 合规门：源码材料未归档时必须 --internal-only，禁止公开 Release
# - staging 构建：verify 全部通过前不动上一次成功产物
# - 签名由内向外，绝不使用 codesign --deep 进行签名
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
APP_NAME="Auto Video Maker"
BUILD_STAGING="$ROOT/build/phase6-staging"
DIST_STAGING="$ROOT/dist/phase6-staging"
FINAL_APP="$ROOT/dist/$APP_NAME.app"
BUILD_LOG="$ROOT/build/phase6-build.log"
INTERNAL_ONLY=0
[[ "${1:-}" == "--internal-only" ]] && INTERNAL_ONLY=1

log() { echo "[build] $*"; }
die() { echo "[build] 失败：$*" >&2; cleanup_staging; exit 1; }
cleanup_staging() {
  rm -rf "$BUILD_STAGING" "$DIST_STAGING"
}
trap 'cleanup_staging' ERR

[[ "$(uname)" == "Darwin" ]] || die "本脚本只能在 macOS 上运行"
[[ "$(uname -m)" == "arm64" ]] || die "本阶段仅支持 Apple Silicon (arm64)"

# ---------------------------------------------------------- 1. manifest（fail closed）
log "校验 FFmpeg manifest 与 vendor 二进制…"
MANIFEST_OUT="$(python3 "$SCRIPT_DIR/validate_manifest.py" --check-binaries)" \
  || die "manifest 校验未通过（见上方输出）"
echo "$MANIFEST_OUT"
SOURCES_ARCHIVED="$(echo "$MANIFEST_OUT" | grep '^SOURCES_ARCHIVED=' | cut -d= -f2)"

# ---------------------------------------------------------- 2. 合规门
if [[ "$SOURCES_ARCHIVED" != "1" && "$INTERNAL_ONLY" != "1" ]]; then
  die "FFmpeg 对应源码材料未归档：只允许 ./packaging/build_app.sh --internal-only（禁止公开 Release）"
fi

# ---------------------------------------------------------- 3. staging 准备
mkdir -p "$ROOT/build" "$ROOT/dist"
cleanup_staging
mkdir -p "$BUILD_STAGING" "$DIST_STAGING"
: > "$BUILD_LOG"
log "构建环境记录（pip freeze → build log）"
python3 -m pip freeze >> "$BUILD_LOG" 2>&1 || true

# ---------------------------------------------------------- 4. PyInstaller
command -v pyinstaller >/dev/null || die "未安装 pyinstaller（pip install -r packaging/requirements-build.txt）"
[[ -f "$SCRIPT_DIR/icon.icns" ]] || die "缺少 packaging/icon.icns（先运行 python3 packaging/make_icon.py）"
log "运行 PyInstaller…"
pyinstaller --noconfirm \
  --distpath "$DIST_STAGING" \
  --workpath "$BUILD_STAGING" \
  "$SCRIPT_DIR/autovideomaker.spec" | tee -a "$BUILD_LOG"

STAGED_APP="$DIST_STAGING/$APP_NAME.app"
[[ -d "$STAGED_APP" ]] || die "PyInstaller 未产出 .app"

# ---------------------------------------------------------- 5. 注入 FFmpeg（Contents/MacOS/bin）
log "注入 FFmpeg/ffprobe 到 Contents/MacOS/bin/…"
BIN_DIR="$STAGED_APP/Contents/MacOS/bin"
mkdir -p "$BIN_DIR"
cp "$SCRIPT_DIR/vendor/ffmpeg" "$BIN_DIR/ffmpeg"
cp "$SCRIPT_DIR/vendor/ffprobe" "$BIN_DIR/ffprobe"
chmod 755 "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"

# ---------------------------------------------------------- 6. 许可证材料
log "注入 NOTICES 与 LGPL 文本…"
RES_DIR="$STAGED_APP/Contents/Resources"
mkdir -p "$RES_DIR/licenses"
cp "$SCRIPT_DIR/THIRD_PARTY_NOTICES.txt" "$RES_DIR/THIRD_PARTY_NOTICES.txt"
# Qt/PySide6 许可证（LGPL + GPL 全文 + PYSIDE6-NOTICE + SHA 记录）。
# 来源顺序：当前 distribution 优先 → packaging/licenses/ 中经 manifest
# SHA-256 校验的官方 vendored 副本 → 两者均不可用即失败。
# 绝不联网下载、绝不 pip install/降级 PySide6、绝不手写全文。
python3 "$SCRIPT_DIR/find_qt_licenses.py" \
  --output-dir "$RES_DIR/licenses" \
  --licenses-dir "$SCRIPT_DIR/licenses" \
  || die "Qt/PySide6 许可证注入失败（fail closed，见上方输出；vendored 副本填写方法见 README）"

# ---------------------------------------------------------- 7. 由内向外签名（绝不 --deep）
log "签名（由内向外，ad-hoc）…"
sign() {  # sign <相对包内路径>
  local target="$STAGED_APP/$1"
  codesign --force -s - "$target"
  echo "[signed] $1" | tee -a "$BUILD_LOG"
}
# 7.1 最内层：注入的独立二进制（必须单独重签）
sign "Contents/MacOS/bin/ffmpeg"
sign "Contents/MacOS/bin/ffprobe"
# 7.2 嵌套代码：Frameworks 与动态库
while IFS= read -r -d '' item; do
  rel="${item#"$STAGED_APP"/}"
  codesign --force -s - "$item"
  echo "[signed] $rel" >> "$BUILD_LOG"
done < <(find "$STAGED_APP/Contents/Frameworks" \
          \( -name "*.framework" -o -name "*.dylib" -o -name "*.so" \) \
          -print0 2>/dev/null || true)
# 7.3 顶层 .app
codesign --force -s - "$STAGED_APP"
echo "[signed] (top-level app)" >> "$BUILD_LOG"

# ---------------------------------------------------------- 8. 自检（含 codesign --verify --deep --strict）
log "运行 verify_bundle…"
"$SCRIPT_DIR/verify_bundle.sh" "$STAGED_APP" | tee -a "$BUILD_LOG" \
  || die "verify_bundle 未通过"

# ---------------------------------------------------------- 9. 晋级正式产物
log "全部通过，替换正式产物…"
rm -rf "$FINAL_APP"
mv "$STAGED_APP" "$FINAL_APP"
rm -rf "$BUILD_STAGING" "$DIST_STAGING"
if [[ "$INTERNAL_ONLY" == "1" ]]; then
  log "标记：INTERNAL-ONLY 构建（禁止作为公开 Release 分发）"
  touch "$ROOT/dist/INTERNAL_ONLY_BUILD"
else
  rm -f "$ROOT/dist/INTERNAL_ONLY_BUILD"
fi
log "完成：$FINAL_APP"
log "下一步：./packaging/make_dmg.sh"
