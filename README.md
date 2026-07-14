# Auto Video Maker

本地运行的自动视频生成桌面应用（MVP 开发中）。

产品文档位置：

- 根目录：`AGENTS.md`（AI 开发规则入口）、`TASK.md`（当前任务）
- `docs/`：`PRODUCT_SPEC.md`、`ARCHITECTURE.md`、`DEVELOPMENT_RULES.md`、`ACCEPTANCE_TESTS.md`

产品概述、目标用户等详见 `docs/PRODUCT_SPEC.md`。

## 当前状态

Phase 1 已完成：桌面应用基础结构与本地项目系统。

已实现：

- PySide6 首页（新建项目、打开项目、最近项目空状态、设置占位按钮）
- 新建项目窗口（项目名称、文案、视频比例、输出目录，含输入校验）
- Project / ProjectSettings / Scene 数据模型
- 项目保存为 `project.json`（UTF-8，保留中文，原子写入）
- 打开已有项目
- 基础日志
- 单元测试与集成测试

尚未实现（后续阶段）：文案拆分、图片搜索、TTS、字幕、FFmpeg 视频生成、打包。

## 开发环境要求

- Python 3.12（代码兼容 3.10+，验收以 3.12 为准）
- macOS Apple Silicon（第一验收平台；代码跨平台）

## 安装与运行

```bash
cd auto-video-maker
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 启动应用
python -m auto_video_maker.app
# 或安装后直接运行
auto-video-maker
```

## 运行测试

```bash
python -m pytest tests/ -v
```

UI 冒烟测试在无图形环境下使用离屏模式：

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -v
```

## 项目工作目录结构

新建项目后，在所选输出目录下生成：

```
输出目录/
└── 项目名称/
    ├── project.json
    ├── assets/      # 图片素材
    ├── audio/       # 配音
    ├── subtitles/   # 字幕工作文件
    ├── temp/        # 临时渲染文件
    ├── output/      # 最终交付（final_video.mp4 / subtitles.srt / credits.txt）
    └── logs/        # 日志
```

## 代码结构

```
src/auto_video_maker/
├── app.py            # 入口
├── ui/               # 界面（不含业务逻辑）
├── models/           # 数据模型
├── services/         # 业务逻辑（ProjectManager）
├── providers/        # 外部服务接口（后续阶段创建）
├── infrastructure/   # 日志、配置等
└── utils/            # 通用工具（校验）
```

## 已知限制

- 最近项目列表仅在当前会话内有效，重启应用后为空（持久化在后续阶段实现）
- 设置按钮为占位，暂无设置功能
- 尚未生成任何视频相关内容（属后续阶段）
