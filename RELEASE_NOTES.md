# Auto Video Maker 0.1.0（macOS，Apple Silicon）

> 构建类型：<INTERNAL-ONLY 内部测试 / PUBLIC 公开发布>
> （FFmpeg 对应源码材料归档完成前，仅允许 INTERNAL-ONLY，
> 不得公开分发。）

## 功能

- 中文文案 → 场景拆分（规则式 / 可选 LLM 智能分镜）
- AI 英文关键词推荐 → Openverse 开放许可图片搜索与下载 / 本地图片
- 中文配音（edge-tts，女声/男声、三档语速、本地缓存）
- 自动同步字幕（SRT，长场景多条 cue）
- 一键导出 1080×1920 / 30fps 的 H.264+AAC MP4（字幕烧录、
  模糊背景、轻微缩放动效）+ credits.txt 版权清单

## 系统要求

- Apple Silicon Mac（arm64）
- macOS 最低版本：<以真实验证结果填写；目标 macOS 13>
- 无需安装 Python、Homebrew 或 FFmpeg（应用内已捆绑）
- LLM / 图片搜索 / 配音功能需要网络；LLM 需用户自备 API Key

## 安装与首次启动

1. 打开 DMG，将 Auto Video Maker 拖入 Applications
2. 本版本为未公证的 ad-hoc 签名构建：首次启动请在
   Applications 中**右键 → 打开**；若仍被阻止，可在终端执行：
   `xattr -dr com.apple.quarantine "/Applications/Auto Video Maker.app"`
3. 首次启动会进行 FFmpeg 与配置目录自检，未通过时会给出指引

## 隐私与网络说明

- 智能分镜 / AI 关键词：文案发送至你配置的 LLM 服务（首次使用需确认）
- 配音：文案发送至微软在线语音服务（首次使用需确认）
- 图片搜索：关键词发送至 Openverse
- API Key 仅保存在 macOS 钥匙串；应用不上传你的项目与视频

## 已知限制

- 未签名（ad-hoc）、未公证；仅 Apple Silicon
- 应用体积较大（内含 Qt 与静态 FFmpeg）
- 创建项目后暂不能修改语音/语速
- Windows 版本延后

## 校验

DMG SHA-256 见 SHA256SUMS.txt。
