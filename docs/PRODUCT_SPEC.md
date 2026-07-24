# PRODUCT\_SPEC\.md

# Auto Video Maker 产品需求文档

## 项目目标

开发一个可以安装在普通用户电脑上的桌面软件。

用户输入一段中文文案后，软件能够自动完成：

1. 将文案拆分为多个视频分镜。

2. 为每个分镜生成图片搜索关键词。

3. 从开放许可的图片网站搜索和下载相关图片。

4. 为文案生成中文语音。

5. 自动生成字幕。

6. 将图片、语音和字幕合成为视频。

7. 将最终视频保存为用户电脑上的 MP4 文件。

第一阶段需要能够打包为 macOS Apple Silicon 应用（.app 与 .dmg）。Windows 安装程序在第二阶段提供。

用户不需要安装 Python，不需要注册账号，不需要登录软件。

---

## 目标用户

目标用户是不懂编程，也不熟悉专业视频剪辑软件的普通用户。

用户只需要：

1. 打开软件。

2. 粘贴中文文案。

3. 选择视频比例和语音。

4. 检查自动选择的图片。

5. 点击导出。

6. 获得本地 MP4 视频。

---

## 第一版 MVP 功能

第一版必须支持：

### 3\.1 文案输入

- 用户可以在文本框中粘贴中文文案。

- 每一个非空段落默认视为一个分镜。

- 用户可以预览拆分后的分镜。

- 用户可以修改、删除和调整分镜内容。

### 3\.2 图片搜索

- 每个分镜生成一个搜索关键词。

- 优先使用英文关键词搜索图片。

- 图片来源优先使用 Openverse 或 Wikimedia Commons。

- 图片只从开放许可或明确授权的来源获取。

- 必须保存图片来源页面、作者、许可协议和本地文件路径。

- 不直接抓取 Google Images、Bing Images 或其他搜索引擎结果中的图片。

- 不绕过网站 API、robots\.txt 或访问限制。

- 用户可以重新搜索图片。

- 用户可以使用本地图片替换自动搜索结果。

### 3\.3 自动语音

- 支持中文男声和中文女声。

- 支持调整语速。

- 每个分镜单独生成音频。

- 程序必须获得每段音频的实际时长。

### 3\.4 字幕

- 自动根据文案生成字幕。

- 字幕时间必须与配音同步。

- 支持设置字幕字号和字幕位置。

- 最终字幕默认烧录进视频。

### 3\.5 视频合成

- 使用 FFmpeg 处理视频。

- 第一版支持竖屏 1080×1920。

- 视频帧率为 30fps。

- 输出编码为 H\.264。

- 音频编码为 AAC。

- 图片必须保持原始比例，不允许直接拉伸。

- 图片不足以填满画面时，使用模糊背景和居中主图。

- 图片需要有轻微放大或移动效果。

- 每个分镜时长与对应配音时长一致。

### 3\.6 视频导出

- 用户可以选择保存位置。

- 最终输出 MP4 文件。

- 同时输出图片版权信息文件 credits\.txt。

- 导出过程中显示进度。

- 导出失败时显示可理解的错误信息。

---

## 第一版不包含的功能

第一版不开发以下功能：

- 用户注册

- 用户登录

- 云端数据库

- 云端项目存储

- 在线支付

- 多人协作

- 视频时间轴编辑器

- 手机端应用

- 自动发布到社交媒体

- AI 数字人

- 自动生成复杂动画

- 模板商城

- 多轨视频编辑

- 云端账号体系与应用服务器代管用户 API Key

- 长视频自动剪辑

AI 不得主动加入以上功能。

关于 LLM API Key（已批准的产品决议）：

- 第一版不建立云端账号体系

- 应用服务器不代管用户 API Key

- 允许用户在本机配置自己的 LLM API Key

- API Key 不上传至 Auto Video Maker 自有服务器

- 调用智能分镜时，文案会直接发送至用户配置的 LLM 服务

---

## 技术栈

### 5\.1 编程语言

- Python 3\.12

### 5\.2 桌面界面

- PySide6

### 5\.3 视频处理

- FFmpeg

- ffprobe

禁止使用 MoviePy 作为核心视频处理方案。

### 5\.4 图片处理

- Pillow

### 5\.5 网络请求

- httpx 或 requests

所有请求必须设置：

- timeout

- User\-Agent

- 错误处理

- 有限次数重试

### 5\.6 自动语音

edge\-tts 是 MVP 默认 TTS Provider。

语音模块必须通过 TTS Provider Interface 封装调用，后续可以替换为其他本地或云端 TTS。

UI 不得直接调用 edge\-tts。

Phase 1 不实现 TTS。

### 5\.7 打包

第一阶段使用 PyInstaller 或 pyside6\-deploy，构建 macOS Apple Silicon 应用（.app 与 .dmg）。

Windows 版本属于第二阶段，必须在 Windows 环境中构建。

---

## 项目架构原则

项目必须采用模块化设计。

建议结构：

```Plain Text
src/auto_video_maker/
├── app.py
├── ui/
├── models/
├── services/
├── providers/
├── infrastructure/
└── utils/
```

模块职责：

- ui/：只处理界面展示和用户交互

- models/：数据模型

- services/：业务逻辑

- providers/：图片、TTS、AI 等外部服务接口

- infrastructure/：FFmpeg、日志、文件系统、配置

- utils/：通用工具

长时间任务在 services/ 或 infrastructure/ 中实现任务执行器，不单独建立 workers/。

核心业务逻辑不得直接写在 GUI 按钮事件中。

GUI 只负责：

- 获取用户输入

- 展示项目状态

- 调用核心模块

- 展示进度和错误

---

## 数据模型

每个 Scene 至少包含：

```Python
@dataclass
class Scene:
    scene_id: int
    text: str
    search_keywords: list[str]
    selected_asset: dict | None
    audio_path: Path | None
    duration: float | None
    status: str
```

图片来源、作者、许可等信息放在 selected\_asset 对象内，例如：

```JSON
{
  "provider": "openverse",
  "asset_id": "123",
  "local_path": "assets/scene_001.jpg",
  "source_page": "https://...",
  "author": "Example Author",
  "license": "CC BY 4.0"
}
```

每个 Project 至少包含：

```Python
@dataclass
class ProjectSettings:
    aspect_ratio: str
    resolution: str
    voice: str
    subtitle_enabled: bool
    output_directory: Path

@dataclass
class Project:
    project_version: str
    project_id: str
    project_name: str
    created_at: str
    updated_at: str
    original_script: str
    settings: ProjectSettings
    scenes: list[Scene]
    output: dict
```

---

## 开发规则

1. 每次只实现 TASK\.md 指定的任务。

2. 不得一次性生成整个项目。

3. 不得修改与当前任务无关的文件。

4. 修改现有代码前，必须先读取相关文件。

5. 已经通过测试的功能不得无原因重写。

6. 所有公共函数必须有类型标注。

7. 所有路径使用 pathlib\.Path。

8. 禁止使用 shell=True 执行 FFmpeg。

9. 临时文件必须放在统一的 temp 目录。

10. 失败时不得静默跳过错误。

11. 网络错误必须提供清晰错误信息。

12. FFmpeg 错误必须保留 stderr。

13. 每完成一个阶段，必须更新 README。

14. 重要逻辑必须添加测试。

15. 不得把 API Key 或隐私信息写入源代码。

---

## 开发阶段

### 阶段 1：本地素材命令行版本

- 读取文案。

- 拆分 Scene。

- 使用本地测试图片。

- 生成语音和字幕。

- 合成第一个 MP4。

### 阶段 2：自动图片搜索

- 接入图片搜索接口。

- 下载并验证图片。

- 保存许可证信息。

- 生成 credits\.txt。

### 阶段 3：桌面界面

- 文案输入页面。

- 分镜预览页面。

- 图片替换功能。

- 导出页面。

- 后台导出线程。

- 进度显示。

### 阶段 4：安装包

- 打包 Windows 程序。

- 检查 FFmpeg 路径。

- 检查字体和资源文件。

- 在没有 Python 的电脑上测试安装和运行。

---

## MVP 最终验收标准

第一版被视为完成，必须同时满足以下条件：

1. 软件可以在没有安装 Python 的 macOS Apple Silicon 电脑上启动。

2. 用户不需要注册或登录。

3. 用户可以粘贴至少三段中文文案。

4. 软件能够将文案拆分成三个分镜。

5. 每个分镜能够搜索或选择一张图片。

6. 软件能够生成中文配音。

7. 软件能够生成同步字幕。

8. 软件能够输出 1080×1920 的 MP4。

9. 视频可以使用 QuickTime Player 或 VLC 正常播放。

10. 画面、配音和字幕基本同步。

11. 输出文件保存在用户选择的位置。

12. 软件同时生成 credits\.txt。

13. 网络失败时不会直接崩溃。

14. FFmpeg 失败时会显示错误信息。

15. 用户可以通过 DMG 正常安装应用，并可以将其从 Applications 中删除。

