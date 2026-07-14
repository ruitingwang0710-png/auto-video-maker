# ARCHITECTURE\.md

# System Architecture

## 架构目标

系统应采用模块化、本地优先的桌面应用架构。

第一版本不需要：

- 后端服务器

- 云数据库

- 用户认证服务

- 云端项目同步

- 微服务架构

所有核心工作均由用户电脑完成。

## 建议技术栈

第一版建议使用：

- Desktop UI：PySide6

- Programming Language：Python

- Video Rendering：FFmpeg

- Image Processing：Pillow

- Audio Processing：FFmpeg

- Local Project Storage：JSON 文件

- Local Settings：本地配置文件

- Packaging：PyInstaller 或等效桌面打包方案

- Testing：pytest

这只是推荐实现。

如果开发过程中更换技术栈，必须满足：

1. 可以生成独立安装包

2. 用户不需要安装开发环境

3. 可以调用 FFmpeg

4. 支持异步任务和进度显示

5. 可以稳定处理中文

6. 不引入不必要的服务器

## 系统模块

```Plain Text
Desktop Application
│
├── UI Layer
│   ├── Home Window
│   ├── Project Editor
│   ├── Settings Window
│   └── Progress Dialog
│
├── Application Layer
│   ├── Project Manager
│   ├── Generation Controller
│   └── Task Queue
│
├── Domain Services
│   ├── Script Parser
│   ├── Scene Planner
│   ├── Keyword Generator
│   ├── Image Search Service
│   ├── Asset Download Service
│   ├── TTS Service
│   ├── Subtitle Service
│   └── Video Renderer
│
├── Provider Layer
│   ├── Image Provider Interface
│   ├── TTS Provider Interface
│   └── Optional AI Provider Interface
│
└── Infrastructure
    ├── FFmpeg Runner
    ├── Local File Storage
    ├── Configuration
    ├── Cache
    └── Logging
```

## 模块职责

### 4\.1 Project Manager

负责：

- 新建项目

- 保存项目

- 打开项目

- 更新项目状态

- 管理项目目录

- 检查项目文件完整性

项目必须使用可读格式保存。

建议使用：

```Plain Text
project.json
```

而不是第一版就使用数据库。

### 4\.2 Script Parser

负责：

- 清理输入文本

- 识别段落

- 拆分长句

- 防止单个场景过长

- 保留原始文案

第一版可以使用规则拆分，不必须依赖大语言模型。

基础拆分规则：

- 优先按照段落拆分

- 其次按照句号、问号、感叹号拆分

- 每个场景建议 15 至 60 个中文字符

- 过短句子可以与相邻句合并

- 不得丢失原始文字

### 4\.3 Scene Planner

负责为每个场景生成：

- 场景编号

- 场景文字

- 搜索关键词

- 图片文件位置

- 音频文件位置

- 字幕开始时间

- 字幕结束时间

- 场景时长

- 处理状态

场景数据结构示例：

```Plain Text
{
  "scene_id": 1,
  "text": "人工智能正在改变企业的工作方式。",
  "search_keywords": [
    "artificial intelligence office",
    "business technology"
  ],
  "selected_asset": null,
  "audio_path": null,
  "duration": null,
  "status": "pending"
}
```

### 4\.4 Image Search Service

负责：

- 接收搜索关键词

- 调用图片来源接口

- 返回统一格式的候选图片

- 处理网络错误

- 处理无搜索结果的情况

- 支持更换图片来源

统一返回数据结构：

```Plain Text
{
  "provider": "provider_name",
  "asset_id": "12345",
  "preview_url": "https://example.com/preview.jpg",
  "download_url": "https://example.com/image.jpg",
  "source_page": "https://example.com/photo/12345",
  "author": "Author Name",
  "license": "provider terms",
  "width": 1920,
  "height": 1080
}
```

禁止让 UI 直接调用具体图片服务。

必须通过统一的 Provider Interface 调用，以便后续替换服务。

### 4\.5 Asset Download Service

负责：

- 下载图片

- 检查 MIME 类型

- 检查文件大小

- 检查图片是否损坏

- 创建本地缓存

- 避免重复下载

- 保存素材来源信息

下载失败时：

- 自动重试有限次数

- 尝试下一张候选图片

- 最终失败后提示用户手动选择本地图片

### 4\.6 TTS Service

负责：

- 将场景文字转换为音频

- 获取音频时长

- 缓存已经生成的音频

- 支持语音、语速等参数

- 将不同服务的返回结果统一处理

TTS Provider Interface 应至少包含：

```Plain Text
class TTSProvider:
    def list_voices(self):
        pass

    def synthesize(self, text, voice, output_path, options=None):
        pass
```

TTS 失败不应导致程序直接退出。

MVP 默认 TTS Provider 为 edge\-tts。UI 不得直接调用 edge\-tts，必须通过 TTS Provider Interface 调用，后续可以替换为其他本地或云端 TTS。Phase 1 不实现 TTS。

### 4\.7 Subtitle Service

负责：

- 根据音频时长计算字幕时间

- 输出 SRT

- 生成烧录字幕所需要的数据

- 处理中文自动换行

- 控制每行字幕长度

第一版允许使用场景级字幕。

不要求实现逐字字幕。

### 4\.8 Video Renderer

负责：

- 生成每个场景的视频片段

- 处理图片缩放和裁剪

- 合并场景

- 添加字幕

- 合并语音

- 添加基础转场

- 输出 MP4

- 返回实时进度

推荐输出编码：

- Container：MP4

- Video Codec：H\.264

- Audio Codec：AAC

渲染模块必须通过命令封装调用 FFmpeg。

UI 层不得直接拼接 FFmpeg 命令。

### 4\.9 Task Queue

图片下载、语音生成和视频渲染不能阻塞 UI 主线程。

任务系统必须支持：

- 进度回调

- 错误回调

- 取消任务

- 重试任务

- 查看当前步骤

## 项目数据结构

```Plain Text
{
  "project_version": "0.1",
  "project_id": "uuid",
  "project_name": "Example Project",
  "created_at": "2026-07-12T14:30:00",
  "updated_at": "2026-07-12T14:40:00",
  "settings": {
    "aspect_ratio": "9:16",
    "resolution": "1080x1920",
    "voice": "default",
    "subtitle_enabled": true
  },
  "original_script": "完整原始文案",
  "scenes": [],
  "output": {
    "video_path": null,
    "subtitle_path": null,
    "status": "draft"
  }
}
```

## 数据流

```Plain Text
User Script
    ↓
Script Parser
    ↓
Scene Planner
    ↓
Keyword Generator
    ↓
Image Search
    ↓
Asset Selection
    ↓
TTS Generation
    ↓
Subtitle Generation
    ↓
FFmpeg Rendering
    ↓
Local MP4 Output
```

## 错误处理

所有外部调用必须处理：

- 网络中断

- 请求超时

- API 限额

- API Key 无效

- 图片下载失败

- TTS 生成失败

- FFmpeg 执行失败

- 磁盘空间不足

- 输出目录无权限

错误信息必须包含：

- 出错步骤

- 普通用户可以理解的说明

- 是否可以重试

- 建议解决方式

错误信息示例：

```Plain Text
无法下载第 3 个场景的图片。

可能原因：
1. 当前网络不可用
2. 图片服务暂时无法访问
3. 搜索结果已失效

你可以点击“重新搜索”，或者上传一张本地图片。
```

## 安全要求

- API Key 不得写入源代码

- API Key 不得上传到 Git

- 日志不得显示完整 API Key

- 不执行来自网络的脚本

- 不允许下载可执行文件作为素材

- 文件名必须经过安全处理

- 外部命令参数必须安全转义

- 应限制图片文件大小

- 应验证文件类型

## 打包架构

最终交付物至少包括：

```Plain Text
dist/
├── Auto Video Maker.app
└── Auto-Video-Maker-macOS-arm64.dmg
```

安装后的用户流程：

1. 用户打开安装包

2. 用户将应用拖入 Applications

3. 用户打开应用

4. 用户不需要打开终端

5. 用户不需要安装 Python

6. 用户不需要安装 FFmpeg

7. 用户可以直接创建项目

