#!/bin/bash
# 生成 DMG（create-dmg 优先，hdiutil fallback）+ SHA256SUMS。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
APP="$ROOT/dist/Auto Video Maker.app"
VERSION="0.1.0"
SUFFIX=""
[[ -f "$ROOT/dist/INTERNAL_ONLY_BUILD" ]] && SUFFIX="-internal"
DMG="$ROOT/dist/AutoVideoMaker-${VERSION}-macos-arm64${SUFFIX}.dmg"
STAGING="$ROOT/dist/phase6-dmg-staging"

die() { echo "[dmg] 失败：$*" >&2; rm -rf "$STAGING"; exit 1; }
[[ "$(uname)" == "Darwin" ]] || die "只能在 macOS 上运行"
[[ -d "$APP" ]] || die "找不到 $APP（先运行 build_app.sh）"

rm -rf "$STAGING"
mkdir -p "$STAGING"
DMG_TMP="$STAGING/$(basename "$DMG")"

if command -v create-dmg >/dev/null; then
  echo "[dmg] 使用 create-dmg…"
  create-dmg \
    --volname "Auto Video Maker" \
    --window-size 540 380 \
    --icon-size 100 \
    --icon "Auto Video Maker.app" 130 180 \
    --app-drop-link 400 180 \
    "$DMG_TMP" "$APP" || die "create-dmg 失败"
else
  echo "[dmg] 未安装 create-dmg，使用 hdiutil fallback…"
  VOLDIR="$STAGING/vol"
  mkdir -p "$VOLDIR"
  cp -R "$APP" "$VOLDIR/"
  ln -s /Applications "$VOLDIR/Applications"
  hdiutil create -volname "Auto Video Maker" -srcfolder "$VOLDIR" \
    -ov -format UDZO "$DMG_TMP" || die "hdiutil 失败"
fi

mv "$DMG_TMP" "$DMG"
rm -rf "$STAGING"
( cd "$ROOT/dist" && shasum -a 256 "$(basename "$DMG")" > SHA256SUMS.txt )
echo "[dmg] 完成：$DMG"
[[ -n "$SUFFIX" ]] && echo "[dmg] 注意：INTERNAL-ONLY 构建，禁止公开分发"
cat "$ROOT/dist/SHA256SUMS.txt"
