# Auto Video Maker

本地运行的自动视频生成桌面应用（MVP 开发中）。

产品文档位置：

- 根目录：`AGENTS.md`（AI 开发规则入口）、`TASK.md`（当前任务）
- `docs/`：`PRODUCT_SPEC.md`、`ARCHITECTURE.md`、`DEVELOPMENT_RULES.md`、`ACCEPTANCE_TESTS.md`

产品概述、目标用户等详见 `docs/PRODUCT_SPEC.md`。

## 当前状态

Phase 3 已完成：图片素材系统。

- Phase 1（项目系统）、Phase 2（文案拆分与场景系统）：已完成并通过 macOS 本机验收
- Phase 2.5（LLM 智能分镜）：implementation complete and automated tests
  passed; live LLM provider integration test deferred

已实现：

- PySide6 首页（新建项目、打开项目、最近项目空状态、设置占位按钮）
- 新建项目窗口（项目名称、文案、视频比例、输出目录，含输入校验）
- Project / ProjectSettings / Scene 数据模型
- 项目保存为 `project.json`（UTF-8，保留中文，原子写入）
- 打开已有项目
- 文案清理与规则式拆分（SceneSplitter 接口 + RuleBasedSceneSplitter，
  段落优先、句末标点切分、短句合并、长句再切，无丢字/无重复/顺序不变）
- 场景编辑页面（拆分、编辑、新增、删除、上移下移、保存）
- 覆盖保护（已有场景需确认才能重新拆分）与未保存修改提示（保存/放弃/取消）
- LLM 智能分镜（可选）：设置页配置 OpenAI 兼容服务 → 隐私确认 →
  后台拆分（可取消）→ 预览确认 → 应用；模型只拆分不改写（不变量校验），
  失败可重试或改用规则拆分
- API Key 存 macOS 钥匙串（按服务地址隔离），config.json 不含密钥
- 图片素材系统：为场景生成/编辑搜索关键词（LLM 可选 + 规则兜底）→
  Openverse 搜索开放许可图片（仅 CC0/PDM/BY）→ 查看候选（作者与许可证）→
  下载（15MB 上限、实际格式校验、防 decompression bomb）或本地图片替换 →
  版权元数据完整存入 project.json（相对路径，防路径逃逸）
- 基础日志
- 单元测试与集成测试

尚未实现（后续阶段）：TTS、字幕、FFmpeg 视频生成、credits.txt 导出、打包。

## 智能分镜设置说明

1. 首页点击「设置」，开启「启用智能分镜」
2. 填写 Base URL（OpenAI Chat Completions 兼容服务的根地址，
   例如 `https://api.example.com/v1`；非本机地址必须为 HTTPS）
3. 填写 Model（模型名称）
4. 在 API Key 密码框输入你的 Key 并点击「保存配置」
   （Key 保存在 macOS 钥匙串中，按服务地址区分；留空保存表示保留原 Key；
   「删除已保存 Key」可移除）
5. 打开项目的场景编辑页，点击「智能拆分」；首次会提示
   文案将发送至你配置的模型服务，确认后开始

未启用或未配置时，「智能拆分」按钮置灰，其余功能与之前完全一致。

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
- 场景拆分目前仅有规则式实现；LLM 智能分镜通过 SceneSplitter 接口
  在 Phase 2.5 加入
- 尚未生成任何视频相关内容（属后续阶段）
