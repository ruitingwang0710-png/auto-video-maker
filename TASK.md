# Current Development Task

## 项目目标

构建 Auto Video Maker 的第一个可运行 MVP。

不要一次完成全部功能。

必须按照阶段逐步开发，每完成一个阶段后运行测试和更新任务状态。

## 当前阶段

Phase 2.5：LLM 智能分镜。

（Phase 1、Phase 2 已完成并通过 macOS 本机验收。）

## 阶段目标

在规则式拆分之外，提供可选的 LLM 智能分镜：语义更自然的场景划分。
LLM 只决定拆分点，不改写任何文字；结果必须经用户预览确认后才生效；
LLM 不可用或失败时，可回退到规则式拆分。

## 已批准的产品决议

以下决议已批准并写入 PRODUCT_SPEC：

- 第一版不建立云端账号体系
- 应用服务器不代管用户 API Key
- 允许用户在本机配置自己的 LLM API Key
- API Key 不上传至 Auto Video Maker 自有服务器
- 调用智能分镜时，文案会直接发送至用户配置的 LLM 服务

## 数据流与职责划分

```
原始文案 original_script（永远保留原样）
  ↓
clean_script（script_parser，复用）
  ↓
SmartSplitService（services/，本阶段新增）
  ├── split_with_llm(text)   → LLMSceneSplitter（providers/）
  │                              ↓ 依赖
  │                            LLMClient 接口（providers/）
  │                              └── OpenAICompatibleClient（httpx）
  └── split_with_rules(text) → RuleBasedSceneSplitter（复用，不修改）
  ↓ 输出：仅供预览的 list[str]
预览对话框（用户确认 / 取消 / 改用规则拆分）
  ↓ 用户确认后
唯一的 SceneService（复用 + 最小新增方法 replace_from_texts）
  → 防御性验证、创建 Scene、scene_id、覆盖保护、dirty、保存
  ↓
ProjectManager 保存（复用）

配置与密钥：
config.json（enabled/base_url/model/timeout_seconds/max_retries/
             privacy_confirmed_for_base_url）
SecretStore 接口（按 secret_id 存取）
  ├── MacOSKeychainSecretStore（生产，API Key 存 macOS 钥匙串）
  └── FakeSecretStore（测试）
secret_id 由规范化 base_url 的 SHA-256 生成（不含 API Key）
```

职责边界：

- SmartSplitService 只执行拆分请求：split_with_llm(text) -> list[str]、
  split_with_rules(text) -> list[str]；不弹窗、不等待用户输入、
  不自行决定回退；不创建 Scene、不修改 Project、不维护 dirty 状态
- LLM 失败后由 UI 显示「重试 / 改用规则拆分」，
  用户选择后 UI 再调用对应的服务方法
- SceneService 仍是全项目唯一的场景写入口：Scene 默认字段、
  scene_id、覆盖保护、dirty、保存只能由它管理；
  不得为规则拆分和 LLM 拆分创建两个 SceneService
- 现有 SceneService 公开 API 无接收已确认 list[str] 的方法
  （build_scenes 不写入 Project、无覆盖保护），因此允许增加一个
  最小方法 replace_from_texts(project, texts, *, overwrite=False)；
  不得改变任何已有公开行为
- UI 不得自行创建 Scene，不感知具体拆分器与 LLM 细节
- SceneSplitter 接口保持不变：输入清理后文案，输出 list[str]
- 不把所有 LLM 等同于 OpenAI：OpenAICompatibleClient 只是第一个实现；
  后续新增其他 Provider 不得修改 SceneService 或 UI 的业务接口
- app.py 仍是唯一 composition root；__main__.py 不变

## 硬约束

- LLM 只能拆分原文，不得改写、删除或添加文字
- 每次 LLM 拆分结果必须通过拼接不变量校验：
  normalize_for_comparison("".join(scene_texts))
  ==
  normalize_for_comparison(cleaned_script)
  校验失败视为 LLM 拆分失败，绝不采用
- LLM 结果必须先预览、用户确认后才写入场景；写入仍受覆盖保护约束
- API Key 不得出现在：config.json、project.json、源代码、Git、日志、
  异常信息、UI 普通文本、subprocess 命令参数、环境变量、
  stdout/stderr 中；且不得以任何衍生形式出现
  （前缀、后缀、长度、由真实 Key 生成的掩码均禁止）
- 日志与普通 UI 只能显示固定状态文字：
  「API Key 已配置」「API Key 未配置」「API Key 验证失败」
- LLM 调用不得阻塞 UI 主线程

## 当前任务范围

### 1. LLMClient 接口与 OpenAICompatibleClient（providers/）

- LLMClient 抽象接口：send(prompt) -> str（返回模型原始文本）

HTTP 合约（第一版固定为 OpenAI Chat Completions 兼容接口）：

- base_url 为 API 根地址（例如 https://example.com/v1）
- 请求地址：base_url + /chat/completions
- 请求体至少包含：
  {"model": "...", "messages": [{"role": "user", "content": "..."}],
   "temperature": 0}
- 响应从 choices[0].message.content 读取
- 使用 httpx（pyproject.toml 增加依赖）
- 请求必须设置 timeout、User-Agent
- 默认不自动跟随 HTTP 重定向（防止认证头被转发到其他主机）
- 非回环地址必须使用 HTTPS；仅 localhost / 127.0.0.1 / ::1 允许 HTTP
- URL 中禁止包含用户名和密码
- Key 由 SecretStore 提供，仅进入 Authorization 请求头

重试策略（总尝试次数 = 1 + max_retries）：

- 400、401：不重试（分别提示配置/请求有误、Key 无效）
- 429：有限重试，可用时遵守 Retry-After
- 5xx：有限重试
- 网络临时错误、超时：有限重试
- JSON 解析失败、不变量校验失败：不自动重复调用模型，
  提示用户重试或改用规则拆分
- 每类错误给出用户可理解的中文信息

### 2. LLMSceneSplitter（providers/，实现 SceneSplitter）

- 构造提示词：要求模型仅在原文中选择拆分点，以 JSON 字符串数组返回，
  不得改动文字；场景长度软目标 15–60 字写入提示词
  （长度不达标不导致拒绝，硬约束只有不变量）
- 响应解析保持保守，只接受两种形式：
  - 完整 JSON 数组
  - ```json 代码块中的 JSON 数组
  不从任意杂质文本中贪婪提取方括号内容；其余一律视为解析失败
- 对结果执行拼接不变量校验，失败抛出明确异常

### 3. SmartSplitService（services/smart_split_service.py）

- split_with_llm(text) -> list[str]：清理文案并经 LLMSceneSplitter 拆分
- split_with_rules(text) -> list[str]：清理文案并经
  RuleBasedSceneSplitter 拆分
- 只执行请求并返回预览用 list[str]；失败时抛出明确异常
- 不弹窗、不等待用户输入、不自行决定回退
  （「重试 / 改用规则拆分」由 UI 在失败后展示，用户选择后
  UI 调用对应方法）
- 不创建 Scene、不修改 Project、不维护 dirty 状态

### 4. SceneService 最小扩展（services/scene_service.py）

- 新增 replace_from_texts(project, texts, *, overwrite=False)
- 防御性验证（即使上游已校验，唯一写入口必须自我防御）：
  - texts 必须是非空列表
  - 每项必须是 str
  - 每项去除空白后不得为空（不允许写入空场景）
  - 验证失败时 Project、scenes、dirty 状态均不得变化
  - 先完整验证并构建全部 Scene，再一次性替换，
    避免替换到一半失败
- 已有场景且未确认时抛出 ScenesExistError（复用覆盖保护）
- 写入成功后置 dirty
- 不得改变任何已有公开行为（split_script 等签名与行为不变）

### 5. SecretStore 与配置（infrastructure/）

SecretStore（infrastructure/secret_store.py）：

- 抽象接口按 secret_id 存取：
  get(secret_id) / set(secret_id, secret) / delete(secret_id) /
  exists(secret_id)
- secret_id 由规范化 base_url 的 SHA-256 生成，不得包含 API Key
- API Key 按规范化 base_url 分开保存：
  - 修改 base_url 后不得静默复用旧地址的 Key
  - 新地址没有对应 Key 时状态显示「未配置」
  - 切回旧地址时可以重新读取该地址原有的 Key
- 生产实现 MacOSKeychainSecretStore（macOS 钥匙串），必须保证：
  - API Key 不出现在 subprocess 命令参数
  - API Key 不出现在环境变量
  - API Key 不出现在 stdout/stderr
  - API Key 不出现在异常和日志
  - 如果无法安全实现，不得回退为 config.json 或其他明文文件，
    必须停止并报告
- 测试实现 FakeSecretStore

配置（infrastructure/config.py）：

- config.json 只保存 enabled、base_url、model、timeout_seconds、
  max_retries、privacy_confirmed_for_base_url；不含任何密钥
- 原子写入；创建后权限设为 600
- 位于用户配置目录；缺失/损坏/缺字段时容错并回落默认值

### 6. 最小 LLM 设置界面（ui/settings_dialog.py）

将首页"设置"占位按钮实现为最小设置页面：

- 启用智能分镜（开关）、Base URL、Model、API Key 密码输入框、
  保存配置、删除已保存 Key、显示「已配置 / 未配置」固定状态文字
  （状态针对当前 base_url 对应的 Key）

Key 行为规则：

- 打开设置时不得从钥匙串读取 Key 填回密码框；已有 Key 仅显示「已配置」
- 密码框留空并保存：保留原 Key
- 输入新 Key 并保存：替换当前 base_url 对应的 Key
- 只有点击「删除已保存 Key」才删除；空字符串不得意外覆盖或删除 Key
- 不做模型列表、账号登录、云端同步或复杂设置系统

智能拆分入口可用条件（必须同时满足）：

- enabled=true
- base_url 有效
- model 非空
- SecretStore 中存在当前 base_url 对应的 Key

### 7. 隐私确认（与 base_url 绑定）

- config.json 保存 privacy_confirmed_for_base_url（已确认的规范化地址），
  不使用单纯的全局布尔值
- 当前规范化 base_url 与已确认地址一致：不重复提示
- base_url 改变：确认自动失效，下次使用时重新提示
- 提示文案：
  "智能分镜会将当前文案发送至你配置的模型服务。
  请确认文案不包含不希望提交给第三方的信息。"
- 用户确认后记录当前规范化 base_url；用户取消时零网络请求

### 8. 后台执行与取消语义（infrastructure/task_runner.py）

- 后台任务执行器：工作线程执行，完成/失败回调回主线程
- 每次任务分配唯一 task_id（generation token）；取消使该 ID 失效：
  - 点击取消后立即恢复 UI
  - 旧任务晚到的成功或失败回调因 ID 失效全部丢弃
  - 新任务持有新 ID，不能重新激活旧任务结果
  - 取消后不得弹出预览、修改 Scene 或覆盖数据
  - 底层请求最迟在 timeout 后结束，UI 不得继续等待
  - 不虚假声称底层网络请求被立即中断

### 9. 预览与确认界面（ui/scene_preview_dialog.py）

- 展示拆分结果列表（编号 + 文字）
- 「应用」：经 SceneService.replace_from_texts 写入
  （已有场景时仍走覆盖确认）
- 「取消」：不改动任何数据
- 「改用规则拆分」：UI 调用 SmartSplitService.split_with_rules
  重新生成并进入预览

### 10. 场景页与入口接入

- ui/scene_page.py 新增「智能拆分」按钮：条件不满足时置灰并提示；
  可用时走 隐私确认 → 后台 LLM 拆分（可取消）→ 预览 → 确认
- LLM 失败后 UI 显示「重试 / 改用规则拆分」，用户选择后
  调用对应的 SmartSplitService 方法
- 原「拆分文案」按钮行为完全不变
- app.py 组装 SecretStore、Config、LLMClient、LLMSceneSplitter、
  SmartSplitService 并注入

## 断网行为定义

- 应用启动、项目管理、规则拆分在断网时与 Phase 2 完全一致
- 不主动检测网络、不主动弹出错误
- 只有用户明确点击「智能拆分」后才显示网络错误
- 错误后提供「重试」或「改用规则拆分」
- 项目和已有场景不得被修改

## 本次不做

- 除最小设置页面外的任何设置系统（模型列表、账号、云端同步）
- 流式输出、多轮对话、上下文记忆
- Windows Credential Manager 实现（SecretStore 接口已预留）
- LLM 生成搜索关键词（Phase 3）
- LLM 改写、润色、扩写文案
- 图片搜索、TTS、字幕、FFmpeg、视频生成、打包
- Phase 3 及后续功能

## 已确认默认规则（新增部分）

- 延续 Phase 1 / Phase 2 全部既有默认规则
- LLM 接口固定为 OpenAI Chat Completions 兼容 HTTP API
- config.json 存普通配置（原子写入、权限 600）；
  API Key 只存 macOS 钥匙串（经 SecretStore，按 base_url 区分）
- 默认 enabled=false
- 总尝试次数 = 1 + max_retries
- 测试一律使用 FakeLLMClient / FakeSecretStore /
  httpx.MockTransport（或等效测试传输层），不发真实网络请求，
  不读写真实钥匙串，不需要真实 Key

## 计划创建或修改的文件

新建：

- src/auto_video_maker/providers/__init__.py
- src/auto_video_maker/providers/llm_client.py
- src/auto_video_maker/providers/llm_scene_splitter.py
- src/auto_video_maker/services/smart_split_service.py
- src/auto_video_maker/infrastructure/secret_store.py
- src/auto_video_maker/infrastructure/config.py
- src/auto_video_maker/infrastructure/task_runner.py
- src/auto_video_maker/ui/settings_dialog.py
- src/auto_video_maker/ui/scene_preview_dialog.py
- tests/unit/test_llm_client.py
- tests/unit/test_llm_scene_splitter.py
- tests/unit/test_smart_split_service.py
- tests/unit/test_secret_store.py
- tests/unit/test_config.py
- tests/unit/test_task_runner.py
- tests/unit/test_settings_dialog.py
- tests/integration/test_llm_split_workflow.py

修改：

- pyproject.toml（增加 httpx 依赖）
- src/auto_video_maker/app.py（composition root：按配置组装注入）
- src/auto_video_maker/services/scene_service.py
  （仅新增 replace_from_texts，最小改动）
- src/auto_video_maker/ui/main_window.py（设置按钮接入设置页面）
- src/auto_video_maker/ui/scene_page.py（智能拆分入口）
- tests/unit/test_scene_service.py（新增 replace_from_texts 测试）
- tests/unit/test_ui_smoke.py
- README.md（LLM 配置说明与阶段状态）

不修改：

- SceneSplitter 接口、RuleBasedSceneSplitter
- SceneService 的既有公开行为（只新增方法）
- models、project_manager、script_parser

## 测试要求

全部使用 FakeLLMClient / FakeSecretStore / httpx.MockTransport，
不得发真实网络请求，不得读写真实钥匙串。至少覆盖：

解析与不变量：
1. 完整 JSON 数组且通过不变量校验 → 拆分成功
2. ```json 代码块包裹的 JSON 数组 → 正确解析
3. 杂质文本夹带 JSON（非上述两种形式）→ 解析失败，不贪婪提取
4. 改写 / 删除 / 添加文字 → 不变量拒绝（三种各测）
5. 非 JSON / 空响应 / 空数组 → 拆分失败

HTTP 客户端：
6. 请求地址为 base_url + /chat/completions，请求体含
   model/messages/temperature=0，响应取 choices[0].message.content
7. 400、401 不重试；429 有限重试且遵守 Retry-After；
   5xx、超时、网络错误有限重试；总尝试次数 = 1 + max_retries
8. 不跟随重定向；非回环地址拒绝 HTTP；回环地址允许 HTTP；
   URL 含用户名密码被拒绝

服务与数据：
9. SmartSplitService：split_with_llm 成功返回预览 list[str]；
   split_with_rules 独立可用；失败抛出异常而不自行回退；
   全程不修改 Project、不产生 dirty
10. SceneService.replace_from_texts：默认字段与编号正确、
    已有场景未确认时抛 ScenesExistError、确认后写入并置 dirty
11. replace_from_texts 防御性验证：非列表/空列表/非 str 项/
    空白项均被拒绝，且拒绝时 Project、scenes、dirty 均无变化
12. SceneService 既有全部行为不变（既有测试保持通过）
13. 预览取消后项目数据无任何改动

取消与任务：
14. task_runner 完成、失败、取消路径
15. 取消后旧任务结果晚到 → 被丢弃；随后启动新任务 → 新结果正常处理、
    旧结果不被重新激活

配置与密钥：
16. config.json 不含 Key；原子写入；权限 600；
    缺失/损坏/缺字段容错回落默认值
17. SecretStore：按 secret_id 保存、读取、删除、exists
    （FakeSecretStore）；secret_id 由规范化 base_url 生成且不含 Key
18. 切换 base_url 后不误用旧地址的 Key：新地址无 Key 时不可用且
    显示「未配置」；切回旧地址时能读取原有 Key
19. 日志与异常信息不含 Key 及任何衍生形式（前缀/后缀/长度/掩码），
    仅固定状态文字
20. 设置页面：打开时密码框为空且不回填；留空保存保留原 Key；
    新 Key 替换；仅删除按钮删除；空字符串不覆盖不删除
21. 智能拆分可用条件四项齐备才可用，缺任一项置灰

隐私与断网：
22. 首次确认后记录当前规范化 base_url；同地址不重复提示
23. 修改 base_url 后确认失效，重新提示；用户取消时零网络请求
24. 未启用/未配置时，规则式流程与 Phase 2 一致（断网不弹窗）

回归：
25. Phase 1 / Phase 2 全部既有测试保持通过

## 完成标准

- 配置并启用后：隐私确认 → 智能拆分 → 预览 → 确认 → 保存 全流程可用
- 应用启动、项目管理、规则拆分在断网时与 Phase 2 完全一致，
  网络错误只在用户主动使用智能拆分后出现
- 不变量校验拦截一切改写/丢字/加字的 LLM 结果
- LLM 调用后台执行、基于 task_id 可取消，旧结果晚到必被丢弃
- API Key 仅存于 macOS 钥匙串，按规范化 base_url 区分；
  切换地址不会误发旧 Key；十类禁区（见硬约束）均无 Key 及衍生形式
- MacOSKeychainSecretStore 若无法满足密钥不落 argv/env/stdout/stderr，
  停止并报告，不得回退明文文件
- HTTP 合约、HTTPS 限制、重定向与 URL 凭据规则全部生效
- 重试策略按错误类型分类执行，总尝试次数 = 1 + max_retries
- SmartSplitService 不含任何用户交互决策
- Scene 创建、覆盖保护、dirty、保存仍只由唯一 SceneService 管理；
  replace_from_texts 的防御性验证与"先验证后一次性替换"生效
- SceneService 既有公开行为未变（仅新增 replace_from_texts）
- app.py 仍是唯一 composition root
- 全部测试通过（含既有测试）
- README 已更新配置说明与阶段状态
- MacOSKeychainSecretStore 的真实钥匙串读写列入 macOS 手动验收项，
  自动测试不写入真实用户钥匙串

## 交付报告

完成后按以下格式回复：

Completed / Files created / Files changed / Tests executed /
Test results / How to run / Manual verification / Known limitations /
Recommended next task

## 后续阶段

### Phase 3：图片素材系统

- 自动生成搜索关键词
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
