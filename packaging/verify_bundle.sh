#!/bin/bash
# .app 打包自检：敏感信息扫描（文本与路径分开）、必需文件、签名验证。
#
# 用法：
#   ./packaging/verify_bundle.sh "<path>/Auto Video Maker.app"
#   ./packaging/verify_bundle.sh --self-test        # 干跑自测（可在 Linux 运行）
#
# 发现疑似密钥/敏感数据 → 退出非零（不得只警告）。
set -euo pipefail

FAILURES=0
# self-test 替身开关：只在脚本内部处理 --self-test 时置 1；
# 无条件初始化为 0，外部环境变量无法注入绕过正式验证。
SELF_TEST_MODE=0

note() { echo "[verify] $*"; }
violation() { echo "[verify] 违规：$*" >&2; FAILURES=$((FAILURES + 1)); }

# ---------------- 平台相关检查（可注入替身；正常模式一律真实执行）
arch_is_arm64() {  # $1: 二进制路径
  if [[ "$SELF_TEST_MODE" == "1" ]]; then
    note "（self-test 替身）arch_is_arm64 $(basename "$1") → 通过"
    return 0
  fi
  lipo -archs "$1" 2>/dev/null | grep -q "arm64"
}

bundled_ffmpeg_runs() {  # $1: ffmpeg 路径
  if [[ "$SELF_TEST_MODE" == "1" ]]; then
    note "（self-test 替身）bundled_ffmpeg_runs → 通过"
    return 0
  fi
  "$1" -version >/dev/null 2>&1
}

codesign_strict_ok() {  # $1: .app 路径（--deep --strict 仅用于验证阶段）
  if [[ "$SELF_TEST_MODE" == "1" ]]; then
    note "（self-test 替身）codesign --verify --deep --strict → 通过"
    return 0
  fi
  codesign --verify --deep --strict --verbose=2 "$1"
}

spctl_log_only() {  # $1: .app 路径；internal/ad-hoc 构建只记录，绝不作为失败条件
  if [[ "$SELF_TEST_MODE" == "1" ]]; then
    return 0
  fi
  spctl -a -vv "$1" 2>&1 | sed 's/^/[spctl] /' || true
}

# 禁止出现在包内的路径/目录名（路径检查，与文本扫描分开）
FORBIDDEN_NAMES=(
  "config.json" "project.json" ".venv" "tests" "vendor"
  "__pycache__" ".pytest_cache"
)
FORBIDDEN_DIRS=("audio" "assets" "temp" "output")
# 文本内容扫描：常见 API Key 前缀与用户主目录绝对路径、个人项目路径片段
SECRET_REGEX='sk-[A-Za-z0-9_-]{8,}|gsk_[A-Za-z0-9_-]{8,}|api_key[[:space:]]*[:=][[:space:]]*"[^"]{8,}'
PERSONAL_PATH_REGEX='/Users/[a-zA-Z0-9_]+/|视频剪辑自动化'
TEXT_EXTENSIONS=("py" "txt" "json" "plist" "md" "cfg" "ini" "toml" "srt")

check_app() {
  local app="$1"
  [[ -d "$app" ]] || { violation "找不到 .app：$app"; return; }

  note "① 禁止项：路径与目录名检查"
  for name in "${FORBIDDEN_NAMES[@]}"; do
    while IFS= read -r hit; do
      violation "包内存在禁止项：${hit#"$app"/}"
    done < <(find "$app" -name "$name" 2>/dev/null)
  done
  for dir in "${FORBIDDEN_DIRS[@]}"; do
    while IFS= read -r hit; do
      # 仅限目录（Qt 资源里可能有同名文件，目录才是项目数据特征）
      violation "包内存在疑似项目数据目录：${hit#"$app"/}"
    done < <(find "$app" -type d -name "$dir" 2>/dev/null)
  done

  note "② 敏感文本扫描（仅文本文件，避免二进制随机字节误报）"
  local find_expr=()
  for ext in "${TEXT_EXTENSIONS[@]}"; do
    find_expr+=(-name "*.${ext}" -o)
  done
  unset 'find_expr[${#find_expr[@]}-1]'
  while IFS= read -r file; do
    if LC_ALL=C grep -E -q "$SECRET_REGEX" "$file" 2>/dev/null; then
      violation "疑似密钥内容：${file#"$app"/}"
    fi
    if LC_ALL=C grep -E -q "$PERSONAL_PATH_REGEX" "$file" 2>/dev/null; then
      violation "疑似个人绝对路径：${file#"$app"/}"
    fi
  done < <(find "$app" -type f \( "${find_expr[@]}" \) 2>/dev/null)

  note "③ 必需文件检查"
  local bin_dir="$app/Contents/MacOS/bin"
  for binary in ffmpeg ffprobe; do
    if [[ ! -x "$bin_dir/$binary" ]]; then
      violation "缺少可执行文件：Contents/MacOS/bin/$binary"
    fi
  done
  # Qt/PySide6 NOTICE 必须存在
  [[ -f "$app/Contents/Resources/THIRD_PARTY_NOTICES.txt" ]] \
    || violation "缺少 THIRD_PARTY_NOTICES.txt"

  # Qt/PySide6 许可证合规：LGPL + GPL 全文、PYSIDE6-NOTICE、构建时 SHA 记录
  local licenses_dir="$app/Contents/Resources/licenses"
  local lgpl_file="$licenses_dir/LGPL-3.0.txt"
  local gpl_file="$licenses_dir/GPL-3.0.txt"
  if [[ ! -d "$licenses_dir" ]]; then
    violation "缺少 Resources/licenses/ 目录（Qt/PySide6 合规）"
  fi
  if [[ ! -f "$lgpl_file" ]]; then
    violation "缺少 LGPL-3.0.txt（Qt/PySide6 合规）"
  elif [[ ! -s "$lgpl_file" ]]; then
    violation "LGPL-3.0.txt 为空（注入内容缺失）"
  elif ! LC_ALL=C grep -q "GNU LESSER GENERAL PUBLIC LICENSE" "$lgpl_file" \
       || ! LC_ALL=C grep -qi "Version 3, 29 June 2007" "$lgpl_file"; then
    violation "LGPL-3.0.txt 内容无效（缺 LGPL 标题或 Version 3, 29 June 2007）"
  fi
  if [[ ! -f "$gpl_file" ]]; then
    violation "缺少 GPL-3.0.txt（LGPLv3 引用 GPLv3 条款）"
  elif [[ ! -s "$gpl_file" ]]; then
    violation "GPL-3.0.txt 为空（注入内容缺失）"
  elif ! LC_ALL=C grep -q "GNU GENERAL PUBLIC LICENSE" "$gpl_file" \
       || ! LC_ALL=C grep -qi "Version 3, 29 June 2007" "$gpl_file"; then
    violation "GPL-3.0.txt 内容无效（缺 GPL 标题或 Version 3, 29 June 2007）"
  fi
  [[ -s "$licenses_dir/PYSIDE6-NOTICE.txt" ]] \
    || violation "缺少 PYSIDE6-NOTICE.txt"
  # 构建时 SHA 记录复核：两个许可证文件必须与构建时记录一致
  if [[ ! -s "$licenses_dir/licenses_record.sha256" ]]; then
    violation "缺少 licenses_record.sha256（构建时 SHA 记录）"
  else
    local sha_check=""
    if command -v shasum >/dev/null 2>&1; then sha_check="shasum -a 256"
    elif command -v sha256sum >/dev/null 2>&1; then sha_check="sha256sum"
    fi
    if [[ -n "$sha_check" ]]; then
      (cd "$licenses_dir" && $sha_check -c licenses_record.sha256 >/dev/null 2>&1) \
        || violation "许可证文件 SHA 与构建时记录（licenses_record.sha256）不一致"
    fi
  fi

  # Frameworks 必须保持独立动态组件形态（.framework / .dylib），未静态合并
  if [[ ! -d "$app/Contents/Frameworks" ]]; then
    violation "缺少 Frameworks/（Qt 必须保持独立框架形态）"
  elif ! find "$app/Contents/Frameworks" \
        \( -name "*.framework" -o -name "*.dylib" \) 2>/dev/null | grep -q .; then
    violation "Frameworks/ 未包含独立动态组件（.framework/.dylib）"
  fi
  [[ -f "$app/Contents/Info.plist" ]] || violation "缺少 Info.plist"

  if [[ "$(uname)" == "Darwin" ]]; then
    note "④ 架构与可执行性"
    for binary in ffmpeg ffprobe; do
      if [[ -x "$bin_dir/$binary" ]]; then
        arch_is_arm64 "$bin_dir/$binary" || violation "$binary 不是 arm64"
      fi
    done
    if [[ -x "$bin_dir/ffmpeg" ]]; then
      bundled_ffmpeg_runs "$bin_dir/ffmpeg" \
        || violation "包内 ffmpeg -version 执行失败"
    fi
    note "⑤ 签名验证（--deep --strict 仅用于验证阶段）"
    codesign_strict_ok "$app" \
      || violation "codesign --verify --deep --strict 未通过"
    spctl_log_only "$app"  # 仅记录；internal/ad-hoc 构建不以此失败
  else
    note "④⑤ 跳过架构/签名检查（非 macOS 环境）"
  fi
}

self_test() {
  note "self-test：只测试纯逻辑（路径/敏感文本/必需文件）；"
  note "平台相关检查（架构/签名/spctl/ffmpeg 执行）使用确定性替身，"
  note "不依赖 vendor 二进制、Xcode、真实 .app 或网络"
  SELF_TEST_MODE=1
  local tmp; tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT

  # --- 违规样本：应被全部检出 ---
  local bad="$tmp/Bad.app"
  mkdir -p "$bad/Contents/MacOS" "$bad/Contents/Resources"
  echo '{"llm": true}' > "$bad/Contents/Resources/config.json"      # 禁止项
  mkdir -p "$bad/Contents/Resources/tests"                           # 禁止项
  echo 'api_key = "sk-abcdefghij1234567890"' \
    > "$bad/Contents/Resources/leak.py"                              # 密钥
  echo '/Users/someone/视频剪辑自动化/x' \
    > "$bad/Contents/Resources/path.txt"                             # 个人路径
  mkdir -p "$bad/Contents/Resources/licenses"
  echo 'named LGPL but wrong content' \
    > "$bad/Contents/Resources/licenses/LGPL-3.0.txt"                # 无效 LGPL 正文
  echo 'named GPL but wrong content' \
    > "$bad/Contents/Resources/licenses/GPL-3.0.txt"                 # 无效 GPL 正文
  local bad_out; bad_out="$(FAILURES=0; check_app "$bad" 2>&1)" || true
  local detected
  detected="$(echo "$bad_out" | grep -c "违规" || true)"
  if [[ "$detected" -lt 4 ]]; then
    echo "[self-test] 失败：违规样本仅检出 $detected 项（期望 ≥4）" >&2
    echo "$bad_out" >&2
    exit 1
  fi

  # --- 合规样本：除 macOS 专属项外不应有敏感/缺文件违规 ---
  local good="$tmp/Good.app"
  mkdir -p "$good/Contents/MacOS/bin" \
           "$good/Contents/Frameworks/QtCore.framework" \
           "$good/Contents/Resources/licenses"
  printf '#!/bin/sh\nexit 0\n' > "$good/Contents/MacOS/bin/ffmpeg"
  printf '#!/bin/sh\nexit 0\n' > "$good/Contents/MacOS/bin/ffprobe"
  chmod +x "$good/Contents/MacOS/bin/ffmpeg" "$good/Contents/MacOS/bin/ffprobe"
  echo "notices" > "$good/Contents/Resources/THIRD_PARTY_NOTICES.txt"
  # 合规 LGPL/GPL 全文替身：非空且含标题与 v3 版本行（不使用真实 PySide6）。
  printf 'GNU LESSER GENERAL PUBLIC LICENSE\n  Version 3, 29 June 2007\n' \
    > "$good/Contents/Resources/licenses/LGPL-3.0.txt"
  printf 'GNU GENERAL PUBLIC LICENSE\n  Version 3, 29 June 2007\n' \
    > "$good/Contents/Resources/licenses/GPL-3.0.txt"
  printf 'PySide6 licence notice (self-test stub)\n' \
    > "$good/Contents/Resources/licenses/PYSIDE6-NOTICE.txt"
  # 构建时 SHA 记录：用真实哈希生成，验证复核路径本身
  local sha_gen=""
  if command -v shasum >/dev/null 2>&1; then sha_gen="shasum -a 256"
  elif command -v sha256sum >/dev/null 2>&1; then sha_gen="sha256sum"
  fi
  ( cd "$good/Contents/Resources/licenses" \
    && $sha_gen LGPL-3.0.txt GPL-3.0.txt > licenses_record.sha256 )
  # Frameworks 独立动态组件替身。
  printf 'dummy\n' > "$good/Contents/Frameworks/QtCore.framework/QtCore"
  echo "plist" > "$good/Contents/Info.plist"
  local good_out; good_out="$(FAILURES=0; check_app "$good" 2>&1)" || true
  if echo "$good_out" | grep -q "违规"; then
    echo "[self-test] 失败：合规样本被误报" >&2
    echo "$good_out" >&2
    exit 1
  fi
  note "self-test 通过（违规检出 $detected 项，合规样本零误报）"
  exit 0
}

if [[ "${1:-}" == "--self-test" ]]; then
  self_test
fi

[[ $# -ge 1 ]] || { echo "用法：$0 <App 路径> | --self-test" >&2; exit 2; }
check_app "$1"
if [[ "$FAILURES" -gt 0 ]]; then
  echo "[verify] 共 $FAILURES 项违规，构建必须失败" >&2
  exit 1
fi
note "全部检查通过"
