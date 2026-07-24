# Current Development Task

## 项目目标

构建 Auto Video Maker 的第一个可运行 MVP。

不要一次完成全部功能。

必须按照阶段逐步开发，每完成一个阶段后运行测试和更新任务状态。

## 当前阶段

Phase 5：视频生成。

阶段状态：

- Phase 1–4：已完成并通过 macOS 本机验收（含真实 Groq 与 edge-tts）

## 阶段目标

把每个场景的图片、配音和字幕合成为视频片段，合并输出
H.264 + AAC 的 MP4，同时生成 credits.txt。
场景时长严格等于配音实际时长；图片绝不拉伸；
显示真实进度并支持取消；失败时保留可理解的 FFmpeg 错误。

## 数据流与职责划分

```
Project（scenes: 图片 + 配音 + 时长）
  ↓ 导出前校验（FFmpeg 能力预检 + 缺图片/缺配音 → 拒绝并列场景号）
SubtitleService（复用）→ 重新生成标准 SRT（subtitles/subtitles.srt）
  ↓
VideoRenderService（services/，本阶段新增）
  ├── 为每个场景生成局部 SRT（时间从 00:00:00,000 起）
  ├── 逐场景片段（一次编码完成：图片适配 + Ken Burns + 配音 +
  │   场景字幕烧录）；内容寻址缓存 temp/clips/clip_<hash24>.mp4
  ├── concat 合并（stream copy，不再编码视频）
  └── 最终仅 faststart/remux
  ↓ 全程经 FFmpegRunner（infrastructure/，唯一的 FFmpeg 封装）
CreditsService（services/，本阶段新增）→ credits.txt
  ↓ staging 事务式落盘（见导出事务）
output/final_video.mp4 + output/subtitles.srt + output/credits.txt
  ↓
ProjectManager.set_video_path（最小新增）→ 写引用并原子保存
```

职责边界：

- UI 不得拼接 FFmpeg 命令（ARCHITECTURE 4.8）；命令构造、执行、
  进度解析、进程终止、**一切路径转义**只在 FFmpegRunner
- 渲染编排只在 VideoRenderService；credits 只在 CreditsService
- output.video_path / subtitle_path 写入只经 ProjectManager 最小方法
- 渲染经 TaskRunner 后台执行（进度通道为最小扩展，见下）
- app.py 仍是唯一 composition root；__main__.py 不变

## 派生产物失效矩阵（核心，已裁决）

全部只清引用，不删除历史输出或缓存文件；全部经统一 Service：

| 操作 | audio_path/duration | subtitle_path | video_path |
|---|---|---|---|
| 修改场景文字 | 清（该场景） | 清 | 清 |
| 添加/删除/重排/整体替换场景 | 不动 | 清 | 清 |
| 重新生成任一场景音频 | 写新值 | 清 | 清 |
| 更换场景图片（set_scene_asset） | 不动 | **不清** | 清 |

- 失效后 UI 不得再显示"已生成/已导出"状态

## 字幕路径规范（已裁决）

- 项目标准字幕始终为 subtitles/subtitles.srt；
  Project.output.subtitle_path 始终引用该路径，
  不得改指到 output 副本
- 导出时复制副本到 output/subtitles.srt（sidecar）
- subtitle_enabled=False 时：不烧录进视频，但仍生成标准 SRT
  与 output/subtitles.srt 副本

## FFmpeg 检测与能力预检（已裁决）

查找顺序：

1. config.json 可选 ffmpeg_path（配置后 ffprobe 先在同目录寻找，
   再查独立 ffprobe_path 配置，最后 PATH）
2. 应用内 bin/ 目录（Phase 6 捆绑预留）
3. 系统 PATH

能力预检（-version 之外必须验证）：

- 编码器：libx264、aac
- 滤镜：zoompan、boxblur、subtitles、scale、overlay
- 启用字幕但缺少 subtitles/libass 能力时必须拒绝导出并明确提示，
  不得静默生成无字幕视频
- 预检失败：导出置灰/拒绝，给出安装指引（macOS: brew install ffmpeg）
- 不自动下载 FFmpeg；开发阶段用系统 FFmpeg，捆绑属 Phase 6

## 渲染规格（已裁决）

画面：

- 分辨率取 settings.resolution（9:16 → 1080x1920 为第一验收目标；
  16:9 → 1920x1080 同机制支持）；30fps CFR
- 前景：保持原始比例缩放至画面内（contain），居中，绝不拉伸
- 背景：同一图片 cover 裁满 + boxblur=20:2
- Ken Burns：居中缩放 1.00 → 1.08 线性，贯穿场景全程，无平移；
  先超采样再 zoompan，避免抖动

帧数与时长规则：

- 全部时间计算使用整数毫秒
- 音频时长是场景时长的权威来源
- 场景输出帧数必须向上取整：
  frame_count = ceil(duration_ms × fps / 1000)
- 视频轨必须覆盖完整音频，绝不能短于音频；
  不得因 -shortest、帧数不足或取整误差截断音频
- 输出时允许以音频结束点收尾
- 场景片段经 ffprobe 后时长误差仍须 ≤ 50ms；
  最终视频与全部场景时长总和误差 ≤ 200ms
- 视频结束不得有长时间黑屏

编码：

- H.264：libx264，preset medium，CRF 20，yuv420p
- AAC：192kbps，44.1kHz
- MP4 容器，movflags +faststart

字幕烧录（单次编码方案，已裁决）：

- VideoRenderService 为每个场景从标准 SRT 派生局部 SRT
  （时间自 00:00:00,000 起）
- 每个场景片段在第一次编码时同时完成图片动效 + 配音 + 场景字幕烧录
- 所有片段编码参数一致，concat 用 stream copy 合并；
  最终只 faststart/remux，不再次编码视频
- 字体：force_style 请求 PingFang SC、Outline=2、MarginV≈80；
  字体缺失允许 libass 系统回退，但必须记录不含隐私的警告日志，
  不得静默忽略
- subtitles/libass 执行失败必须判定导出失败；
  不得自动关闭字幕继续导出

## 片段缓存（已裁决）

缓存键（canonical JSON → SHA-256 前 24 位）必须使用真实内容摘要：

- image_sha256（图片文件内容摘要）
- audio_sha256（音频文件内容摘要）
- duration_ms、resolution、fps
- effect_params、encode_params
- renderer_schema_version
- 字幕开关、该场景局部字幕文本与局部时间轴、字幕样式

不得仅用 asset_id、local_path 或文件名判断内容未变化。

缓存命中验证（ffprobe，全部满足才复用）：

- 视频流存在、音频流存在
- 宽高正确、帧率正确
- 视频编码与像素格式符合要求
- 时长误差 ≤ 50ms

存放 temp/clips/clip_<hash24>.mp4；渲染中的片段先写
clip_<hash24>.part.mp4，验证通过后原子移动为 .mp4。
只清引用不删旧片段；最终合并结果不缓存。

临时文件命名（已裁决，写死）：所有临时 MP4 输出一律采用
*.part.mp4 命名（成功后原子移动为正式 .mp4）；
不得使用 *.mp4.part 等依赖 .part 结尾推断容器的命名；
若个别命令确需其他命名，必须显式指定 -f mp4。

## FFmpeg 路径安全（已裁决）

- UI 与 Service 不自行转义路径；filtergraph、subtitle、concat 的
  路径处理全部集中在 FFmpegRunner
- 复杂滤镜优先通过临时 filter_complex_script 文件传递
- concat 列表由统一方法安全生成（转义规则单一实现）
- 必须测试含中文、空格、单引号的路径
- 继续禁止 shell=True（既定规则）

## 导出事务（staging，已裁决）

1. 预检（FFmpeg 能力 + 素材完整性：每场景必须有图片文件与
   配音文件/时长；缺失 → 拒绝并列出场景号与缺失类型）
2. SubtitleService 重新生成标准 SRT（不依赖旧引用），
   更新 subtitle_path 引用
3. 在 temp/export_<task_id>/ 下生成（临时 MP4 必须保留可识别的
   .mp4 结尾，FFmpeg 依扩展名推断容器）：
   final_video.part.mp4、subtitles.part.srt、credits.part.txt
4. 全部生成并经最终 ffprobe 验证（视频流、音频流、分辨率、fps、
   codec、时长误差 ≤ 200ms）后，才原子替换 output/ 三个文件
5. 成功后：ProjectManager.set_video_path → 原子保存 project.json
6. 取消或失败：删除本次 staging 与当前半成品；保留已完成片段缓存；
   保留上一次成功输出；不修改 Project、不写 video_path
7. 视频已成功生成但 project.json 保存失败：不删除最终视频，
   明确提示"视频已生成，但项目状态保存失败"；
   Project 不得被静默标记为保存成功

## 进度与取消（已裁决）

TaskRunner 最小扩展：

- run(...) 增加可选 on_progress 回调，保持既有 API 兼容
- 进度范围 0–100、单调不减、可节流（避免 UI 高频刷新）
- 任务成功、失败或取消后不得继续派发进度
- 既有完成/失败/取消/晚到丢弃语义不变，既有测试不得修改

FFmpeg 进程管理：

- FFmpeg 作为独立进程组启动；-progress 管道解析实时进度
- 取消：task_id 失效 + terminate 整个进程组，3 秒未退出则 kill
- 取消后删除半成品、保留缓存片段、不写 Project、UI 立即恢复
- 失败：保留 stderr 末尾摘要并入错误信息（不含用户隐私）

## credits.txt 格式（已裁决）

- UTF-8 纯文本；头部为项目名称 + 生成时间（ISO 8601）
- CreditsService 接收可注入的 timestamp_provider（Clock），
  自动测试不得依赖真实当前时间
- 每张选中图片一段：场景 N：标题 / 作者（author_url）/
  来源页 source_page / 许可证 license license_version（license_url）/
  attribution 原文
- provider="local" 的图片标注"用户提供的本地图片"

## 当前任务范围

### 1. FFmpegRunner（infrastructure/ffmpeg_runner.py）

- 三级定位、版本检测、编码器/滤镜能力预检
- 命令执行：参数列表拼装（无 shell=True）、-progress 解析回调、
  stderr 环形缓存、进程组 terminate/kill
- 路径安全：filtergraph/subtitle/concat 转义与
  filter_complex_script、concat 列表统一生成
- 错误分类：未安装、能力缺失、执行失败（含 stderr 摘要）、被取消

### 2. VideoRenderService（services/video_render_service.py）

- validate_export(project) -> list[缺失说明]
- render(project, on_progress, cancel_token) -> 输出相对路径
- 局部 SRT 派生、场景片段命令构造（单次编码含字幕）、
  内容摘要缓存键、缓存验证、concat stream copy、faststart remux、
  staging 事务与最终 ffprobe 验证

### 3. CreditsService（services/credits_service.py）

- 按已裁决格式生成 credits.txt（staging 内），返回路径；
  timestamp_provider 可注入

### 4. ProjectManager 最小扩展

- set_video_path(project, path) / clear_video_path(project)
  （相对路径校验、防逃逸，模式同 subtitle_path）

### 5. SceneService 失效矩阵实施

- 按失效矩阵扩展既有失效路径；set_scene_asset 只清 video_path
- 不得改变其余既有公开行为

### 6. TaskRunner 最小扩展

- 可选 on_progress（0–100、单调、节流、终态后停止派发）

### 7. 导出 UI（ui/export_dialog.py + scene_page 入口）

- 「导出视频」按钮：预检失败或素材不全时置灰并提示原因
- 导出对话框：输出位置、总进度条、当前步骤、取消、
  完成后「打开输出文件夹」
- UI 不拼命令、不转义路径、不读写 JSON、不直接改 Project

### 8. app.py 注入

- 组装 FFmpegRunner、VideoRenderService、CreditsService 并注入

## 断网行为定义

- 视频渲染全程本地，断网不受影响

## 本次不做

- 安装包、DMG、FFmpeg/字体捆绑（Phase 6）
- 转场效果、背景音乐、水印
- 字幕字号/位置的用户设置（用默认安全值）
- 多分辨率同时导出、GPU 编码
- 视频预览播放器（导出后用系统播放器打开）
- Windows 支持
- Phase 6 及后续功能

## 已确认默认规则（新增部分）

- 延续既有全部默认规则
- FFmpeg 三级查找 + 能力预检、不自动下载
- 前景 contain + 背景 cover 模糊（boxblur=20:2）
- Ken Burns 1.00→1.08 居中缩放
- libx264 medium CRF20 yuv420p + AAC 192k 44.1kHz + faststart，30fps
- 单次编码烧录（场景局部 SRT）+ concat stream copy + 仅 remux 收尾
- 片段缓存键用文件内容 SHA-256；命中经 ffprobe 六项验证
- staging 事务式导出；保留上一次成功输出
- 帧数 ceil 取整，音频为时长权威来源，视频轨不得短于音频
- 临时 MP4 统一 *.part.mp4 命名（保留容器可识别的 .mp4 结尾）
- 取消 = task_id 失效 + 进程组 terminate/kill + 清理半成品
- 自动测试一律 FakeFFmpegRunner；真实 FFmpeg 集成测试自动跳过
  （测试项目路径含中文与空格）

## 计划创建或修改的文件

新建：

- src/auto_video_maker/infrastructure/ffmpeg_runner.py
- src/auto_video_maker/services/video_render_service.py
- src/auto_video_maker/services/credits_service.py
- src/auto_video_maker/ui/export_dialog.py
- tests/unit/test_ffmpeg_runner.py
- tests/unit/test_video_render_service.py
- tests/unit/test_credits_service.py
- tests/unit/test_project_manager_video.py
- tests/integration/test_export_workflow.py（Fake 全流程）
- tests/integration/test_real_ffmpeg_render.py（无 FFmpeg 自动跳过）

修改：

- src/auto_video_maker/infrastructure/task_runner.py（仅进度通道）
- src/auto_video_maker/infrastructure/config.py
  （可选 ffmpeg_path/ffprobe_path）
- src/auto_video_maker/services/project_manager.py（video_path 两方法）
- src/auto_video_maker/services/scene_service.py（失效矩阵）
- src/auto_video_maker/ui/scene_page.py（导出入口）
- src/auto_video_maker/app.py
- tests/unit/test_ui_smoke.py、test_scene_service_audio.py
  （失效矩阵回归）、test_task_runner.py（仅新增进度用例，
  既有用例不得修改）
- README.md

## 测试要求（FakeFFmpegRunner，不要求测试机安装 FFmpeg）

预检与定位：
1. 三级查找顺序；ffmpeg_path 配置后 ffprobe 先同目录再独立配置再 PATH
2. 能力预检：FFmpeg 存在但缺 libx264/aac/libass(subtitles) 时
   预检失败并明确提示；启用字幕缺 libass → 拒绝导出

命令构造：
3. 场景片段：分辨率/fps/contain 不拉伸/模糊背景/zoompan 参数/
   帧数按 ceil(duration_ms × fps / 1000) 计算/无 shell=True
   （参数列表断言）
4. 编码参数：libx264/CRF/yuv420p/AAC/faststart
5. 字幕：启用时片段命令含 subtitles 滤镜与 force_style 字体，
   且只编码一次（concat 为 stream copy）；
   subtitle_enabled=False 时无烧录但仍输出 sidecar SRT
6. 路径安全：中文、空格、单引号路径可成功构造滤镜命令与
   concat 列表（转义正确）

缓存：
7. 内容摘要键：文件内容变化但路径不变 → 缓存失效；
   字幕文本/局部时间轴/开关/样式变化 → 缓存失效；
   场景重排（内容不变）→ 不失效
8. 命中验证：流缺失、宽高、fps、codec、时长不符 → 重渲染

导出事务：
9. validate_export：缺图片/缺配音列出场景号并拒绝
10. 调用序列：预检 → 重新生成标准 SRT → 局部 SRT → 片段 →
    concat → remux → credits → 原子替换 → set_video_path → 保存
11. staging 失败/取消：保留上一次成功输出、删除本次 staging、
    Project 未被写
12. project.json 保存失败：最终视频保留，明确报错，
    不静默标记成功
13. 最终 ffprobe 验证（Fake 断言六项被检查）

credits：
14. 格式、许可字段完整、本地图片标注、UTF-8、
    注入 timestamp_provider 后时间可预期

失效矩阵：
15. 更换图片只清 video_path、不清 subtitle_path；
    其余矩阵行为逐项验证；只清引用不删文件
16. set_video_path/clear_video_path：相对路径与防逃逸校验

进度与取消：
17. TaskRunner 进度：0–100 单调、节流、终态后不再派发、
    既有语义回归（既有用例不修改）
18. 取消：进程组 terminate 被调用、3 秒后 kill、半成品清理、
    缓存保留、无进度继续派发

真实 FFmpeg（自动跳过）：
19. 含中文与空格的项目路径下：两场景小图短音频 → 输出 MP4
    经 ffprobe 验证视频流、音频流、分辨率、fps、codec、
    时长误差 ≤ 200ms

帧数与临时文件：
20. duration_ms × fps / 1000 非整数时 frame_count 必须向上取整，
    视频轨不得短于音频（不得截断音频结尾）
21. 所有临时 MP4 输出必须具有可识别的 .mp4 扩展名（*.part.mp4），
    或命令中明确包含 -f mp4

回归：
22. 既有全部测试保持通过

## 完成标准

- macOS（装有 FFmpeg）可一键导出：竖屏 1080×1920、30fps、
  H.264+AAC、图片不拉伸、模糊背景、缩放动效、字幕烧录、
  时长与配音一致、无长黑屏
- output/ 三件套齐备（final_video.mp4 / subtitles.srt / credits.txt）
- subtitle_path 始终指向 subtitles/subtitles.srt
- 二次导出未变更场景走片段缓存，明显加速
- 单次编码方案生效：concat 为 stream copy，最终仅 remux
- staging 事务生效：失败/取消保留上一次成功输出
- 进度真实推进、取消即时生效且清理干净
- 失效矩阵全部生效并有测试锁定
- FFmpeg 缺失或能力不足时不崩溃、提示清晰
- 全部自动测试通过且不要求测试机安装 FFmpeg
- README 已更新阶段状态

## macOS 人工验收流程

1. brew 安装 FFmpeg；启动应用确认导出入口可用
2. 用 Phase 4 验收项目（三场景，图片+配音齐备）点「导出视频」
3. 观察进度推进 → 完成后打开 output/final_video.mp4：
   QuickTime 播放，检查画面比例、模糊背景、缩放动效、
   字幕同步、配音完整、结尾无黑屏
4. 检查 output/subtitles.srt 与 credits.txt 内容
5. 立即再次导出 → 明显加速（片段缓存）
6. 导出中点取消 → 立即停止、无 .part 残留、上次输出仍在、
   可再次导出
7. 删除某场景图片引用后导出 → 被拒绝并列出场景号
8. 修改某场景文字 → 重新生成配音 → 再导出 → 内容更新
9. 关闭字幕开关的项目导出 → 视频无字幕但 output/ 仍有 SRT
10. 16:9 项目导出横屏验证
11. 用含空格与中文的输出目录重复步骤 2–3
12. 临时改名 ffmpeg（模拟缺失）→ 导出置灰且提示安装指引

## 交付报告

Completed / Files created / Files changed / Baseline test result /
Tests executed / Final test result / How to run /
macOS manual verification steps / Known limitations /
Recommended next task

## 后续阶段

### Phase 6：打包与发布

- 打包 FFmpeg 与字幕字体、构建 macOS App、构建 DMG
- 在干净设备测试、编写安装说明、记录已知限制
- Windows 10 / Windows 11 打包放到后续阶段进行
