# TASK\.md

# Current Development Task

## 项目目标

构建 Auto Video Maker 的第一个可运行 MVP。

不要一次完成全部功能。

必须按照阶段逐步开发，每完成一个阶段后运行测试和更新任务状态。

## 当前阶段

Phase 1：建立桌面应用基础结构与本地项目系统。

## 当前任务范围

本次只完成：

1. 创建项目目录结构

2. 创建最小 PySide6 桌面应用

3. 实现首页

4. 实现新建项目窗口

5. 实现项目数据模型

6. 将项目保存为 `project.json`

7. 实现打开已有项目

8. 添加基础日志

9. 添加单元测试

10. 提供本地启动命令

## 本次不做

- 图片搜索

- 图片下载

- AI 关键词生成

- TTS

- 字幕

- FFmpeg 视频生成

- 安装包

- 背景音乐

- 转场

- 云端功能

## 已确认默认规则

- 中文项目名称允许使用

- 项目名称禁止 `/`、`\`、`..`、控制字符及 Windows 保留名称

- 设置按钮在 Phase 1 只做占位提示，不实现设置功能

- `9:16` 对应 `1080×1920`，`16:9` 对应 `1920×1080`

- 项目格式初始版本为 `0.1`

- 时间统一使用 ISO 8601 格式保存

- JSON 文件使用 UTF\-8，保留中文字符，不强制转义为 Unicode 编码

## 必须创建的目录

```Plain Text
src/auto_video_maker/
├── app.py
├── ui/
├── models/
├── services/
├── infrastructure/
└── utils/

tests/
├── unit/
└── integration/
```

## 数据模型

必须至少实现：

### Project

```Plain Text
project_version
project_id
project_name
created_at
updated_at
original_script
settings
scenes
output
```

### ProjectSettings

```Plain Text
aspect_ratio
resolution
voice
subtitle_enabled
output_directory
```

### Scene

```Plain Text
scene_id
text
search_keywords
selected_asset
audio_path
duration
status
```

## 首页要求

首页显示：

- 应用名称

- 新建项目按钮

- 打开项目按钮

- 最近项目区域

- 设置按钮

最近项目功能在本阶段可以为空状态，但界面不得崩溃。

## 新建项目窗口

用户可以输入：

- 项目名称

- 文案

- 视频比例

- 输出目录

验证规则：

- 项目名称不能为空

- 文案不能为空

- 输出目录不能为空

- 输出目录不可写时必须提示错误

- 不允许通过项目名称写入项目目录之外的位置

## 保存要求

保存后生成：

```Plain Text
selected_output_directory/
└── project_name/
    ├── project.json
    ├── assets/
    ├── audio/
    ├── subtitles/
    ├── temp/
    ├── output/
    └── logs/
```

## 测试要求

至少完成以下测试：

1. 创建有效项目

2. 项目名称为空

3. 文案为空

4. 输出目录为空

5. 保存并重新读取项目

6. 中文项目名称

7. 中文路径

8. 非法项目名称

9. 无写入权限目录

10. `project.json` 内容完整

## 完成标准

只有满足以下条件，本任务才算完成：

- 应用可以启动

- 首页可以打开

- 可以创建项目

- 可以保存 `project.json`

- 可以重新打开项目

- 单元测试通过

- 没有使用假数据冒充保存功能

- 没有要求用户手动修改 JSON

- 开发说明已经添加到 README

## 交付报告

完成后请按照以下格式回复：

```Plain Text
Completed:
- ...

Files created:
- ...

Files changed:
- ...

Tests executed:
- ...

Test results:
- ...

How to run:
- ...

Manual verification:
- ...

Known limitations:
- ...

Recommended next task:
- ...
```

## 后续阶段

### Phase 2：文案和场景系统

- 文案拆分

- 场景编辑

- 搜索关键词

- 场景保存

- 场景排序

### Phase 3：图片素材系统

- 图片 Provider Interface

- 图片搜索

- 候选图片

- 图片下载

- 本地图片上传

- 素材版权信息

### Phase 4：语音和字幕

- TTS Provider Interface

- 中文语音

- 音频缓存

- 音频时长

- 场景级字幕

- SRT 输出

### Phase 5：视频生成

- FFmpeg 检测

- 图片转视频

- 图片裁剪

- 场景合并

- 添加语音

- 添加字幕

- 生成 MP4

- 显示渲染进度

- 取消渲染

### Phase 6：打包与发布

- 打包 FFmpeg

- 构建 macOS App

- 构建 DMG

- 在干净设备测试

- 编写安装说明

- 记录已知限制

- Windows 10 / Windows 11 打包放到后续阶段进行

