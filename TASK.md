# Current Development Task

## 项目目标

构建 Auto Video Maker 的第一个可运行 MVP。

不要一次完成全部功能。

必须按照阶段逐步开发，每完成一个阶段后运行测试和更新任务状态。

## 当前阶段

Phase 2：文案拆分与场景系统。

（Phase 1 已完成并通过 macOS 本机验收。）

## 阶段目标

用户输入中文文案后，程序能够按照稳定、可预测的规则拆分为多个 Scene，
并允许用户在界面中查看、编辑、排序和保存场景。

## 数据流与职责划分

```
原始文案 original_script（永远保留原样）
  ↓
clean_script（script_parser）
  ↓
SceneSplitter → list[str]（场景文字列表）
  ↓
SceneService → list[Scene]（统一创建 Scene）
  ↓
UI 编辑（ScenePage，只经 SceneService 操作）
  ↓
ProjectManager 保存 project.json
```

- script_parser.py 只提供低层级纯函数：clean_script、
  normalize_for_comparison、标点识别与文本切分辅助函数
- scene_splitter.py 负责 SceneSplitter 抽象接口、RuleBasedSceneSplitter，
  以及完整的段落拆分、短句合并、长句再切和硬切策略
- 不得在两个模块中各自实现一套完整拆分流程
- SceneSplitter 统一接口：输入清理后的文案字符串，输出 list[str]
- RuleBasedSceneSplitter 和未来的 LLMSceneSplitter 都只返回 list[str]，
  不负责创建 Scene
- Scene 数据模型只能由 SceneService 统一创建：将 list[str] 转换为
  list[Scene]，生成连续 scene_id，设置 search_keywords=[]、
  selected_asset/audio_path/duration=null、status=pending
- 两个拆分器必须遵守相同的 SceneSplitter 接口并返回场景文字列表；
  SceneService 将结果统一转换为相同的 Scene 数据模型

## 依赖注入

app.py 是唯一 composition root，负责创建并注入：

```
RuleBasedSceneSplitter
        ↓ 注入
SceneService
        ↓ 注入
MainWindow / ScenePage
```

- SceneService 不得在内部自行实例化 RuleBasedSceneSplitter
- UI 不得自行实例化拆分器，不得直接调用规则函数
- UI 只依赖已注入的 SceneService
- __main__.py 只负责调用 app.main()，
  不得自行创建 SceneSplitter、SceneService 或 UI 对象
- 测试中使用 FakeSceneSplitter / StubSceneSplitter 验证接口可替换性

## 当前任务范围

### 1. 文案清理

- 保留原始文案 original_script，清理只作用于拆分用副本
- 统一换行符为 LF
- 去除每行首尾空白（含全角空格），行内连续空白压为单个空格
- 连续空行视为一个段落分隔
- 不得丢失有效中文内容

### 2. 规则式文案拆分

- 优先按段落（空行）拆分
- 段落过长时按句末标点（。！？!?…，含其后紧跟的引号、括号收尾符）拆分，
  标点归前句
- 场景长度建议 15–60 个字符（按去除空白后的字符数计）
- 短句合并规则：
  - 少于 15 字时优先向后合并
  - 如果没有后一句，则尝试向前合并
  - 只有合并后不超过 60 字才执行合并
  - 如果前后都无法在 60 字以内合并，允许保留短场景
- 仍超过 60 字的单句按次级标点（，；、：,;）再拆；完全无标点时按 60 字硬拆
- 15–60 字是软目标；无丢字、无重复、顺序不变是硬约束，
  不能为了满足长度而删除或改写文字
- 本阶段不调用大语言模型，不增加 API Key、网络请求、提示词或模型配置

### 3. Scene 数据生成（由 SceneService 统一负责）

继续使用已确定的数据模型：
scene_id、text、search_keywords、selected_asset、audio_path、duration、status

- scene_id 从 1 开始连续编号
- text 保存场景文字
- search_keywords 默认为空列表
- selected_asset 为 null
- audio_path 为 null
- duration 为 null
- status 为 pending

### 4. 场景编辑界面

- 显示场景列表（编号 + 场景文字）
- 可以编辑单个场景文字
- 可以新增场景
- 可以删除场景
- 可以上移和下移场景；第一项上移和最后一项下移必须安全处理，
  可以禁用对应按钮或执行 no-op
- 任何增删移操作后 scene_id 重新从 1 连续编号
- 可以保存到 project.json
- 重新打开项目后场景数据不丢失
- UI 不得直接读写 JSON，必须通过 services 层

### 5. 防止数据丢失

- 从原始文案首次生成场景需要用户明确操作（点击"拆分文案"）
- 已有场景时再次拆分不得静默覆盖：service 层必须先报告"已存在场景"，
  由 UI 提示用户确认覆盖或取消
- original_script 永远保留原样
- 保存失败时显示普通用户可理解的错误信息

### 6. 未保存修改保护

- 编辑、新增、删除或移动场景后，页面标记为未保存（dirty）
- 保存成功后清除未保存状态
- 有未保存修改时，如果用户退出应用、打开其他项目或新建项目，
  应提示：保存 / 放弃 / 取消
- 空白场景文字不得保存，用户应删除该场景或填写内容

## 本次不做

- 接入真实 LLM API（LLMSceneSplitter 的实现属 Phase 2.5）
- 图片搜索、图片下载
- Openverse 或 Wikimedia Commons 接入
- 自动生成搜索关键词（移至 Phase 3）
- TTS、字幕
- FFmpeg、视频预览、视频生成
- 安装打包
- Phase 2.5 及后续功能

## 已确认默认规则

- 中文项目名称允许使用
- 项目名称禁止 `/`、`\`、`..`、控制字符及 Windows 保留名称
- 设置按钮只做占位提示
- `9:16` 对应 `1080×1920`，`16:9` 对应 `1920×1080`
- 项目格式版本为 `0.1`
- 时间统一使用 ISO 8601 格式保存
- JSON 文件使用 UTF-8，保留中文字符，不强制转义
- 场景字数按去除空白后的字符数计算

## 计划创建或修改的文件

新建：

- src/auto_video_maker/services/script_parser.py（clean_script、
  normalize_for_comparison、标点识别与切分等低层级纯函数）
- src/auto_video_maker/services/scene_splitter.py（SceneSplitter 接口 +
  RuleBasedSceneSplitter，完整拆分策略）
- src/auto_video_maker/services/scene_service.py（list[str] → list[Scene]、
  增删改移、重编号、覆盖保护、dirty 状态）
- src/auto_video_maker/ui/scene_page.py
- tests/unit/test_script_parser.py
- tests/unit/test_scene_splitter.py
- tests/unit/test_scene_service.py
- tests/integration/test_scene_workflow.py

修改：

- src/auto_video_maker/app.py（composition root：创建并注入拆分器与服务）
- src/auto_video_maker/ui/main_window.py（进入场景编辑的入口、
  未保存修改提示）
- tests/unit/test_ui_smoke.py
- README.md

## 测试要求

至少覆盖：

1. 单段中文文案
2. 多段中文文案
3. 中英文标点混合
4. 空白文案
5. 只有空格或换行
6. 过短句合并（含最后一句向前合并、前后均无法合并时保留短场景）
7. 过长句拆分（次级标点再拆与无标点硬拆）
8. 原文顺序保持
9. 无文字丢失与无文字重复（统一用不变量验证，见下）
10. Scene 默认字段正确
11. scene_id 连续
12. 编辑场景并保存
13. 新增和删除场景
14. 上移和下移场景
15. 第一场景上移和最后场景下移不会破坏顺序
16. 保存后重新读取
17. 已存在场景时防止静默覆盖
18. 场景修改后进入 dirty 状态，保存后清除
19. 空白场景无法保存
20. 使用 FakeSceneSplitter 验证 SceneService 不依赖具体拆分实现

### 不变量测试说明

"无丢字、无重复、顺序不变"统一通过以下不变量验证：

```
normalize_for_comparison("".join(scene_texts))
==
normalize_for_comparison(cleaned_script)
```

不要通过检查字符是否唯一来验证"无重复"，
因为原文本身可能包含重复字符或重复词语。

## 完成标准

只有满足以下条件，本任务才算完成：

- 拆分规则对同一输入产生稳定一致的结果
- 拼接不变量对全部测试输入成立
- 场景可以在界面中编辑、增删、排序并保存
- 重新打开项目后场景数据完整
- 已有场景不会被静默覆盖
- 未保存修改在退出/切换项目时有保存、放弃、取消提示
- original_script 始终保留原样
- 拆分逻辑通过 SceneSplitter 接口调用；SceneService 和 UI 均不依赖
  具体的 SceneSplitter 实现，替换拆分器时无需修改 SceneService 或 UI
- app.py 为唯一的 composition root
- 全部测试通过
- UI 层不包含拆分逻辑和 JSON 读写
- README 已更新阶段状态

## 交付报告

完成后按以下格式回复：

Completed / Files created / Files changed / Tests executed /
Test results / How to run / Manual verification / Known limitations /
Recommended next task

## LLM 扩展点说明（Phase 2 已知限制与预留）

Phase 2 交付后，拆分能力仅有规则式实现。已预留的扩展点：

- SceneSplitter 接口是唯一拆分入口，LLMSceneSplitter 作为新实现加入
  providers/，返回同样的 list[str]，Scene 仍由 SceneService 统一创建，
  不改动 scene_service 和 UI 的调用方式
- LLM 模式默认只能拆分原文，不得改写、删除或添加文字
  （复用 Phase 2 的拼接不变量校验）
- LLM 返回结果必须先预览并由用户确认，不得直接覆盖已有场景
  （复用 Phase 2 的覆盖保护机制）
- LLM 失败时回退到 RuleBasedSceneSplitter
- API Key、提示词、模型配置均在 Phase 2.5 才引入

## 后续阶段

### Phase 2.5：LLM 智能分镜

- LLMSceneSplitter（实现 SceneSplitter 接口，置于 providers/）
- API Key 配置与安全存储（不入源代码、不入 Git）
- 提示词与模型配置
- 拆分结果预览与用户确认
- 只拆分不改写的校验（拼接不变量）
- 失败回退到规则式拆分

### Phase 3：图片素材系统

- 自动生成搜索关键词（从 Phase 2 移入）
- 图片 Provider Interface
- 图片搜索、候选图片、图片下载
- 本地图片上传
- 素材版权信息

### Phase 4：语音和字幕

- TTS Provider Interface、中文语音（edge-tts）
- 音频缓存、音频时长
- 场景级字幕、SRT 输出

### Phase 5：视频生成

- FFmpeg 检测、图片转视频、图片裁剪
- 场景合并、添加语音、添加字幕
- 生成 MP4、渲染进度、取消渲染

### Phase 6：打包与发布

- 打包 FFmpeg、构建 macOS App、构建 DMG
- 在干净设备测试、编写安装说明、记录已知限制
- Windows 10 / Windows 11 打包放到后续阶段进行
