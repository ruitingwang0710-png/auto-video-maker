# Current Development Task

## 项目目标

构建 Auto Video Maker 的第一个可运行 MVP。
必须按照阶段逐步开发，每完成一个阶段后运行测试和更新任务状态。

## 当前阶段

Phase 6：macOS 打包与发布。

阶段状态：Phase 1–5 已完成并通过 macOS 本机真实验收
（LLM/Groq、edge-tts、Openverse、FFmpeg 8.1.2 导出全链路）。

## 阶段目标

把应用打包为可双击启动的 macOS .app 与可分发 DMG：
普通用户无需 Python/虚拟环境/Homebrew，应用内含全部 Python 依赖、
Qt 依赖与兼容的 FFmpeg/ffprobe；打包版能完成完整 MVP 链路；
现有项目文件、缓存与输出格式保持兼容；API Key 继续只存 Keychain。
本阶段不开发新业务功能，不做 Windows。

## 许可证（已裁决）

- 自有代码采用 MIT License；仓库根添加 LICENSE：
  Copyright (c) 2026 Bonnie Wang
- MIT 只覆盖 Auto Video Maker 自有代码，不改变任何第三方组件许可证
- FFmpeg（GPL，含 libx264）的义务单独履行（见合规门一节）；
  本文件不对"独立进程调用是否隔离 GPL 影响"作最终法律结论
- 商业或大规模公开分发前，进行独立的许可证审查

## Packaging architecture

```
dist/
├── Auto Video Maker.app
│   └── Contents/
│       ├── MacOS/
│       │   ├── Auto Video Maker          # PyInstaller 引导
│       │   └── bin/ffmpeg, bin/ffprobe   # 捆绑二进制（已裁决位置）
│       ├── Frameworks/…                  # Python + Qt 依赖（保持独立）
│       └── Resources/
│           ├── icon.icns                 # 临时图标
│           └── THIRD_PARTY_NOTICES.txt
└── AutoVideoMaker-0.1.0-macos-arm64.dmg
```

- FFmpeg/ffprobe 安装位置固定为 Contents/MacOS/bin/，
  不得放在 Contents/Resources/bin（ResourceLocator 与
  verify_bundle 同步按此实现）
- .app 内部一律只读；运行期数据绝不写入应用包
- 运行期目录（全部沿用既有实现，保证升级兼容）：
  - 配置：~/Library/Application Support/AutoVideoMaker/config.json
  - API Key：macOS Keychain（服务名 AutoVideoMaker-LLM，不变）
  - 日志：~/Library/Logs/AutoVideoMaker/app.log（本阶段新增）
  - 项目/缓存/输出：用户自选项目目录（结构不变）

## Build environment pinning（已裁决）

- 构建机：Apple Silicon Mac；Python 3.12 arm64（python.org 安装版）
- 目标最低版本：暂定 macOS 13（Ventura），仅 arm64，不做 universal2
- macOS 13 支持声明门槛：必须满足其一——
  (a) 在 macOS 13 环境构建并测试；或
  (b) 完成全部 Mach-O deployment target 审计，并在真实
      macOS 13 arm64 设备上验证通过
  未完成上述验证前，不得仅凭 Info.plist 宣称支持 macOS 13；
  Release 声明的最低版本以真实验证结果为准
- 打包工具：PyInstaller（spec 文件，版本钉在
  packaging/requirements-build.txt）；构建前 pip freeze 记录进构建日志

## 打包配置（packaging/autovideomaker.spec）

- windowed .app；App Name = Auto Video Maker；
  Bundle ID = com.bonniewang.autovideomaker；Version = 0.1.0
- PySide6：依官方 hook 收集 QtCore/QtGui/QtWidgets 与 platforms/
  styles/imageformats plugins；显式排除未使用模块
  （QtWebEngine、Qt3D、QtMultimedia、QtCharts 等，
  排除清单进 spec 并逐项注释；GPL-only 的 Qt 模块必须排除）
- hidden imports 按实测补齐（edge_tts、mutagen.mp3、aiohttp、
  certifi 等；以打包后自检与干净设备启动为准）
- certifi/SSL：验证 frozen 下 httpx 与 edge-tts 的证书加载
- datas：icon.icns、THIRD_PARTY_NOTICES.txt
- 排除进入包：tests/、docs/、packaging/vendor/、.venv、__pycache__、
  任何项目数据/素材/缓存/输出/个人路径/API Key

## FFmpeg/ffprobe bundling（已裁决）

来源与固定：

- 使用 Martin Riedl Build Server 的 macOS arm64 正式 release，
  固定 FFmpeg 8.1.2；不使用 snapshot/nightly
- ffmpeg 与 ffprobe 分别下载并分别固定 SHA-256
- packaging/ffmpeg_manifest.json 记录：版本、来源、下载 URL、
  SHA-256（两个二进制各自）、架构、构建配置（configure 参数）、
  许可证类别
- 人工下载入 packaging/vendor/（gitignored）；
  普通构建流程不自动下载，仅校验 manifest 与本地文件一致

构建时预检：

- 对 vendor 二进制执行 -version 与能力预检
  （libx264、aac、zoompan/boxblur/subtitles/scale/overlay），
  不满足即构建失败
- 注入 Contents/MacOS/bin/ 并按签名顺序单独签名（见签名一节）

运行期发现：

- FFmpegRunner 既有三级查找不变；app.py 在 frozen 时经
  ResourceLocator 注入 app_bin_dir=Contents/MacOS/bin →
  捆绑版优先于 PATH；config.json 的 ffmpeg_path 覆盖仍最高优先
- 打包版不得依赖 Homebrew、系统 Python 或用户虚拟环境

## FFmpeg 公开发布合规门（已裁决）

- 公开 Release 的前提：能够归档或提供与捆绑二进制**精确对应**的
  FFmpeg、x264、libass 等全部源码、构建配置、补丁与许可证文本
- 若无法获得完整对应源码：该二进制只允许内部测试使用，
  不得用于公开 Release
- 正式发布时如有必要，改为使用固定脚本自行构建 FFmpeg
  （从官方源码 + 记录在案的 configure 参数），以保证源码对应性
- 合规材料（源码获取方式/归档位置）记入 THIRD_PARTY_NOTICES

## Resource locator（最小新增）

- infrastructure/resource_locator.py：
  is_frozen()（sys.frozen）、bundled_bin_dir()
  （frozen 时为 Contents/MacOS/bin）、bundled_resources_dir()、
  bundled_file(name)；开发环境回退仓库路径
- 仅 app.py（composition root）消费其结果注入各服务；
  业务模块不感知 frozen 状态
- 除资源定位与打包兼容所必需外，不修改核心业务逻辑；
  如需改公共接口或数据模型，停止并报告

## Logging（已裁决）

- logging_config 增加 RotatingFileHandler（1MB × 3）写
  ~/Library/Logs/AutoVideoMaker/app.log（目录不存在则创建）
- 控制台 handler 保留；文件日志沿用既有脱敏规则
  （无 API Key 及衍生、无完整文案）
- 设置对话框底部新增「打开日志文件夹」按钮（本阶段唯一 UI 改动）

## App icon and metadata（已裁决）

- 临时 .icns 由脚本生成（占位样式），正式图标延后
- Info.plist：CFBundleName/Identifier/ShortVersionString、
  LSMinimumSystemVersion（按真实验证结果填写）、
  NSHighResolutionCapable、
  LSApplicationCategoryType=public.app-category.video

## Code signing（已裁决）

签名执行顺序（由内向外，不得使用 codesign --deep 进行签名）：

1. 先签最内层独立二进制：Contents/MacOS/bin/ffmpeg、ffprobe
   （PyInstaller 完成后注入的二进制必须重新单独签名）
2. 再签嵌套代码（Qt Frameworks / Python 动态库，如有必要）
3. 最后签顶层 .app

- 本阶段全部为 ad-hoc 签名（-s -）
- 验证阶段才使用 codesign --verify --deep --strict，
  另记录 spctl 状态（不作为通过条件）
- Developer ID 签名与 notarization 明确延后
- 用户 README 提供 Gatekeeper 首启指引（右键打开 /
  xattr -dr com.apple.quarantine）

## PySide6 / Qt LGPL 合规（已裁决）

- THIRD_PARTY_NOTICES 列出实际打包的 Qt/PySide6 模块清单及各自许可证
- 排除未使用的 GPL-only Qt 模块（spec 排除清单联动）
- 附 LGPLv3 全文与 Qt / PySide6 版权通知
- Qt Frameworks 在 .app 内保持独立框架形态，
  不静态合并、不混淆、不重新打包为不可替换形式
- 允许用户在许可证许可范围内替换与调试 LGPL 库
  （框架路径与替换方式在 NOTICES 中说明）
- 提供打包所用 Qt / PySide6 对应版本的源码获取信息
- verify_bundle 检查：Frameworks 目录存在且未被合并、
  许可证/NOTICES 文件在包内存在

## DMG construction（已裁决）

- create-dmg 生成（含 Applications 快捷方式）；
  提供 hdiutil fallback 脚本
- 命名：AutoVideoMaker-0.1.0-macos-arm64.dmg
- 生成后 shasum -a 256 记入 SHA256SUMS.txt

## Third-party licence notices

THIRD_PARTY_NOTICES.txt 必须包含：

- FFmpeg：GPL（含 libx264）授权文本要点、捆绑版本 8.1.2、
  构建来源（Martin Riedl release）、SHA-256、
  与二进制精确对应的源码获取/归档说明（合规门）
- Qt / PySide6：LGPLv3 全文、模块清单、独立框架说明、
  替换方式与源码获取信息
- edge-tts、httpx、Pillow、mutagen 等 Python 依赖许可汇总
- 微软在线语音服务与 Openverse 的服务性质说明
- 不作任何关于 GPL 边界的最终法律结论（见许可证一节）

## Reproducible build commands

- packaging/build_app.sh：清理 build/dist → 校验 vendor FFmpeg
  （manifest SHA-256 + 能力预检）→ pyinstaller spec →
  注入 MacOS/bin 与 NOTICES → 按序签名（内→外）→
  packaging/verify_bundle.sh 自检
- packaging/make_dmg.sh：.app → DMG → SHA-256
- 所有生成物只进 build/ 与 dist/（已在 .gitignore；
  packaging/vendor/ 加入 .gitignore）
- 打包失败不得污染 Git 工作区（脚本失败即清理本次产物）

## 打包后自检（packaging/verify_bundle.sh）

- .app 内不得存在：config.json、tests/、.venv、__pycache__、
  packaging/vendor、任何 project.json/音视频素材/temp/output、
  匹配 sk-/gsk_ 等密钥模式的文本
- 必须存在：Contents/MacOS/bin/ffmpeg 与 ffprobe
  （可执行、arm64、已签名）、Frameworks/（独立形态）、
  NOTICES 与许可证文件、icon.icns
- 包内路径执行 bin/ffmpeg -version 成功
- codesign --verify --deep --strict 通过并记录输出

## 首次启动检查（已裁决）

- 启动时：捆绑 FFmpeg 存在 + 能力预检、配置目录可写检查；
  失败显示指引对话框，不阻止进入首页
- 不主动探测网络（沿用断网原则）；字体缺失仅记录警告
- 网络类功能（LLM/图片/TTS）保持既有的使用时报错与隐私确认

## Upgrade and backward compatibility

- 打包版必须能打开 Phase 1–5 创建的 project.json（列入验收）
- config.json / Keychain 服务名 / 目录结构全部不变：
  从开发版切到打包版时既有配置与 Key 直接可用
- 项目缓存（audio/、temp/clips/）继续有效（内容寻址键未变）

## Test plan（自动测试，不发真实网络请求）

1. ResourceLocator：开发/模拟 frozen（monkeypatch sys）两种状态；
   frozen 时 bundled_bin_dir 指向 Contents/MacOS/bin；
   注入后 FFmpegRunner 查找顺序：config 覆盖 > 包内 bin > PATH
   （回归既有测试）
2. 日志：文件 handler 写入用户日志目录、轮转参数、
   重复初始化不叠加、既有脱敏测试保持通过
3. 首启检查：FFmpeg 缺失/能力不足/配置目录不可写的分类结果
   （Fake 注入，不弹真窗）
4. spec 静态断言：excludes 含 GPL-only 与未用 Qt 模块、
   datas 不含敏感项
5. verify_bundle --self-test 干跑模式纳入单测
6. 既有全部测试保持通过；自动测试不执行真实 LLM/Openverse/edge-tts
7. 打包产物级检查由 verify_bundle.sh 在构建时执行（不进 pytest）

## Clean-machine acceptance（macOS 人工验收）

前置：另一台未安装 Python/Homebrew/FFmpeg 的 Apple Silicon Mac
（降级方案：本机新建用户账户并确认 PATH 无 ffmpeg/python3 私装；
正式结论以干净设备为准）。若最低版本按 macOS 13 发布，
验收设备中必须包含真实 macOS 13 arm64（见构建门槛）。

1. 挂载 DMG → 拖入 Applications → 弹出 DMG
2. 首次启动（按 README 的 Gatekeeper 指引）→ 应用正常出现首页，
   无缺库/缺模块错误（AT-001）
3. 首启检查通过（内置 FFmpeg 被识别；无网络弹窗）
4. 设置页配置 LLM（Groq）→ Key 入 Keychain；
   打开钥匙串确认条目；确认 .app 与 config.json 无 Key
5. 完整 MVP 链路：新建项目（女声/正常）→ 粘贴三段文案 →
   智能拆分（隐私确认）→ AI 关键词 → Openverse 选图下载 →
   生成全部语音（TTS 隐私确认）→ 生成字幕 → 导出视频 →
   QuickTime 播放检查画面/配音/字幕/无黑屏
6. output/ 三件套齐备；credits.txt 内容正确
7. 打开旧项目（拷贝一个 Phase 5 时期的项目目录）→ 正常加载与再导出
8. 断网启动与断网使用本地功能全部正常；网络功能给出友好错误
9. 「打开日志文件夹」可用；日志无 Key/文案泄漏
10. 删除 .app → 重新拖入 → 配置与 Key 仍在（用户数据独立于应用包）

## Release checklist / 发布产物

- dist/Auto Video Maker.app（按序 ad-hoc 签名）
- dist/AutoVideoMaker-0.1.0-macos-arm64.dmg
- dist/SHA256SUMS.txt
- RELEASE_NOTES.md（功能清单、已知限制、系统要求按真实验证填写）
- 用户 README（安装、首次启动/Gatekeeper、FFmpeg 说明、
  网络服务与隐私说明：LLM/Openverse/edge-tts 何时联网、发送什么）
- THIRD_PARTY_NOTICES.txt、LICENSE（MIT，Copyright (c) 2026 Bonnie Wang）
- FFmpeg 合规材料（对应源码归档/获取说明；不满足则本次仅内部测试）
- 干净设备验收记录（十步逐项）

## Rollback and failure cleanup

- 构建失败：脚本删除本次 build/dist 残留，Git 工作区不受影响
- 验收失败：不发布 DMG；问题按既有"停止并报告"流程处理
- 用户侧回滚：删除 .app 即可；用户数据（配置/Key/项目）独立于应用包

## 本次不做

- Windows 打包（明确延后）
- Developer ID 签名、notarization、自动更新、CI 构建
- 正式图标设计、universal2/Intel 支持
- 任何新业务功能；核心业务逻辑仅限资源定位/打包兼容的最小改动

## Known limitations（预期写入 RELEASE_NOTES）

- 未签名（ad-hoc）：首次启动需按指引绕过 Gatekeeper
- 仅 Apple Silicon；最低 macOS 版本以真实验证为准
- 应用体积较大（Qt + FFmpeg 静态二进制）
- LLM/图片/配音功能需网络与（LLM）用户自备 API Key

## 交付报告

Completed / Files created / Files changed / Baseline test result /
Bundle verification result / Tests executed / Final test result /
Build commands used / Artifact list with SHA-256 /
Clean-machine acceptance steps / Known limitations / Recommended next task
