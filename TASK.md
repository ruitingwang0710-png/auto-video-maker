# Current Development Task

## 项目目标

构建 Auto Video Maker 的第一个可运行 MVP。

不要一次完成全部功能。

必须按照阶段逐步开发，每完成一个阶段后运行测试和更新任务状态。

## 当前阶段

Phase 4：语音和字幕。

阶段状态：

- Phase 1、Phase 2、Phase 3：已完成并通过 macOS 本机验收
- Phase 2.5: implementation complete; live Groq integration verified
  during Phase 3 acceptance.

## 阶段目标

为每个场景生成中文配音并获得真实时长；基于时长生成与配音同步的
字幕（SRT）。断网时既有全部功能不受影响；
TTS 失败不崩溃、不影响已完成的场景。

本阶段必须锁死的三条核心规则：

- 文本改变 → 该场景配音引用失效
- 场景集合改变（增/删/重排/重生成音频）→ 字幕引用失效
- 长文本 → 拆成多条 cue，绝不截断或丢字

## 数据流与职责划分

```
Scene.text + ProjectSettings(voice, speech_rate)
  ↓
AudioService（services/，本阶段新增）
  ├── TTSProvider 接口（providers/，本阶段新增）
  │     └── EdgeTTSProvider（MVP 默认实现，edge-tts）
  ├── 内容寻址缓存：audio/tts_<hash24>.mp3
  │     （命中后仍须经 AudioProbe 验证才允许复用）
  └── AudioProbe（infrastructure/，mutagen 读取实际时长）
  ↓ (audio_path 相对路径, duration_seconds)
SceneService.set_scene_audio（最小新增方法）
  → 写入 Scene.audio_path / Scene.duration，置 dirty，
    并按失效规则清空 subtitle_path 引用
  ↓
SubtitleService（services/，本阶段新增）
  → 计算 cue 时间轴（整数毫秒）、中文换行、输出 SRT 文件
  → 只返回相对路径，不修改 Project
  ↓
ProjectManager.set_subtitle_path（最小新增方法，项目级状态）
  → 写入 Project.output.subtitle_path
  → 随后立即经保存流程持久化（保持 SRT 文件与引用一致）
```

职责边界：

- UI 不得直接调用 edge-tts，必须经 TTSProvider 接口（既定决议）
- 合成、缓存、时长只在 AudioService；cue 计算与 SRT 生成只在
  SubtitleService
- SubtitleService 只生成 SRT 并返回相对路径，不直接保存或修改 Project
- Scene 状态（文字、音频、素材）的写入仍只经唯一 SceneService；
  SceneService 只负责 Scene 状态与会话 dirty，不管理项目级输出字段
- Project.output.subtitle_path 是项目级状态，写入与清除只经
  ProjectManager 的最小方法（set_subtitle_path / clear_subtitle_path）；
  UI 不得直接修改 Project.output
- 所有失效操作必须走统一 Service，不允许 UI 直接修改模型
- TTS 网络调用一律经 TaskRunner 后台执行；取消为软取消（见下）
- app.py 仍是唯一 composition root；__main__.py 不变

## TTSProvider 接口（遵循 ARCHITECTURE 4.6）

```
class TTSProvider(ABC):
    def list_voices(self) -> list[TTSVoice]
    def synthesize(self, text, voice_id, rate, output_path) -> None
```

- EdgeTTSProvider 为 MVP 默认实现；后续更换 TTS 服务不得修改
  AudioService 与 UI 的业务接口
- edge-tts 为异步库：在 TaskRunner 工作线程内以 asyncio.run 包装，
  不创建全局事件循环，不阻塞 UI
- 语音映射只在 Provider 内进行：
  - female → zh-CN-XiaoxiaoNeural
  - male   → zh-CN-YunxiNeural
  - default → female（向后兼容）
- 语速三档（模型存储值）："-20%" / "+0%" / "+20%"

## 数据模型变更（已批准的最小扩展）

ProjectSettings 新增字段：

- speech_rate: str = "+0%"
- 严格验证：只允许 "-20%"、"+0%"、"+20%"
- 旧项目缺字段回落 "+0%"；非法值安全回落 "+0%"（记录警告），
  不得把非法值原样传给 Provider
- 模型只保存稳定内部值（voice = "female"/"male"，
  rate = 上述三档）；UI 显示文字（女声/正常等）不得写入模型
- 旧项目 voice="default" 向后兼容，等价 female

Scene 复用既有字段，不新增：

- audio_path：相对项目根目录（如 audio/tts_<hash24>.mp3），
  禁止绝对路径与路径逃逸（复用 Phase 3 规则）
- duration：秒，float，由 AudioProbe 从生成的 mp3 实测

## 派生产物失效规则（核心）

以下失效只清除项目中的引用，不要求立即删除旧缓存文件；
全部经统一 Service 执行：

- 场景文字发生变化（update_scene_text）：
  - 清空该场景的 audio_path 和 duration
  - 清空 project.output.subtitle_path
- 添加、删除、重排场景：
  - 清空 project.output.subtitle_path
- 重新生成任一场景音频（set_scene_audio 写入新值）：
  - 清空 project.output.subtitle_path
- 上述任何失效发生后 UI 不得再显示"已生成"状态

## 当前任务范围

### 1. TTSProvider 与 EdgeTTSProvider（providers/tts_provider.py）

- 接口 + TTSVoice 数据类（voice_id、显示名、性别）
- EdgeTTSProvider：调用 edge-tts 合成 mp3 到指定路径
- 失败分类：网络不可达/超时、服务异常、文本无效，
  每类给出用户可理解的中文错误
- .part 临时文件 + 成功后原子移动（复用 Phase 3 模式）；
  失败与取消均不得留下 .part 或损坏输出
- 有限重试：仅网络类错误，总尝试次数 = 1 + max_retries
- 日志不得记录完整文案或完整服务响应

### 2. AudioProbe（infrastructure/audio_probe.py）

- mutagen MP3.info.length 读取真实时长（秒，float）
- 读取失败、时长不为正、文件缺失/损坏均明确报错
- 可注入替身（FakeAudioProbe）供测试

### 3. AudioService（services/audio_service.py）

缓存键（内容寻址）：

- 对以下结构做规范化稳定序列化（键排序的 canonical JSON）后
  计算 SHA-256，文件名取前 24 位十六进制：
  {schema_version: 1, provider_id: "edge-tts",
   voice_id: <解析后的完整 voice_id>, rate, output_format: "mp3",
   text: <完整场景文字>}
- 任一字段变化 → hash 变化；场景重排不影响 hash

行为：

- generate_for_scene(project, index) -> (audio_path, duration)
- 缓存命中：文件存在且再次通过 AudioProbe 验证 → 直接复用（零合成）；
  验证失败视为未命中
- 未命中 → TTSProvider 合成 → AudioProbe 实测时长
- 返回相对路径与时长；不写 Scene（写入经 SceneService）
- 批量生成（部分成功语义，见下）

批量生成部分成功语义：

- 逐场景顺序执行，进度回调
- 每个场景成功后立即经 SceneService.set_scene_audio 写入内存状态，
  项目置 dirty，但不自动保存 project.json
- 中途失败即停止并报告失败场景；已完成场景保留；
  失败及未开始的场景保持原样
- 重试时有效缓存直接复用，不重新请求网络
- 关闭窗口时沿用既有未保存提示

### 4. SceneService 最小扩展

- set_scene_audio(project, index, audio_path, duration)：
  - audio_path 必须相对且解析后位于项目目录内（复用防逃逸）
  - duration 必须为正数
  - 验证失败时零副作用；成功写入、置 dirty，
    并清空 project.output.subtitle_path（失效规则）
- 既有方法按失效规则扩展行为：
  - update_scene_text：文字实际变化时同时清空该场景
    audio_path/duration，并经 ProjectManager.clear_subtitle_path
    清空字幕引用
  - add_scene / delete_scene / move_scene_up / move_scene_down
    （实际发生变动时）：经 ProjectManager.clear_subtitle_path 清空
  - 失效路径照常置 dirty
- 不得改变其余既有公开行为

### 4b. ProjectManager 最小扩展（项目级状态）

- set_subtitle_path(project, subtitle_path)：
  相对路径与防逃逸校验；写入 Project.output.subtitle_path
- clear_subtitle_path(project)：置为 null
- 生成字幕成功的调用链：SubtitleService 生成 → 经
  ProjectManager.set_subtitle_path 写入 → 立即保存 project.json
  （SRT 文件已落盘，引用同步落盘保持一致，不留悬挂的未保存状态）
- 不得改变 ProjectManager 其余既有公开行为

### 5. SubtitleService（services/subtitle_service.py）

输入校验：

- 任一场景缺 duration → 明确报错，提示先生成语音

cue 划分规则（解决"单 cue + 限长 + 不丢字"矛盾）：

- 场景文字 ≤ 32 字符：生成一条 cue
- 超过 32 字符：拆成多条连续 cue
- 每条 cue 最多 2 行，每行最多 16 个字符，每条最多 32 个字符
- 优先在中文标点附近分段；无法按标点分段时硬切
- 不得丢字、改字或改变顺序（拼接不变量校验）

时间轴规则：

- 全部时间计算使用整数毫秒（start_ms / end_ms），
  不得累计浮点秒
- 场景内多条 cue 按字符数比例分配该场景音频时长：
  chunk_duration = scene_duration × chunk字符数 / scene总字符数
- 每个场景最后一条 cue 的 end_ms 必须等于该场景结束时间
  （消除比例分配的舍入误差）
- 跨场景按时长累计；顺序正确、互不重叠、start_ms < end_ms
- 全项目最后一条 cue 不得超过音频总时长

输出：

- 标准 SRT（UTF-8、序号从 1、时间格式 HH:MM:SS,mmm、空行分隔）
  写入 subtitles/subtitles.srt（覆盖旧文件）
- 只返回相对路径；由调用方经 ProjectManager.set_subtitle_path
  写入项目并立即保存

### 6. TTS 隐私确认

- 首次点击生成语音时提示：
  "生成配音会将场景文案发送至微软在线语音服务（edge-tts）。
  请确认文案不包含不希望提交给第三方的信息。"
- config.json 记录（与 LLM 确认状态完全分离，不得混用）：
  - tts_privacy_confirmed: bool
  - tts_privacy_provider: "edge-tts"
  - tts_privacy_notice_version: 1
- Provider 或 notice_version 变化时必须重新确认
- 用户拒绝时：零网络请求、零后台任务提交、零 .part 临时文件

### 7. UI

新建项目窗口（ui/new_project_dialog.py）：

- 语音下拉（显示：女声/男声 → 存储：female/male）
- 语速下拉（显示：慢/正常/快 → 存储：-20%/+0%/+20%）

场景页（ui/scene_page.py）新增配音与字幕区域：

- 每场景显示配音状态（未生成 / 已生成 + 时长秒数）；
  失效规则触发后必须立即回到"未生成"显示
- 「生成语音」（当前场景）与「生成全部语音」（批量 + 进度 + 可取消）
- 「生成字幕」：全部场景有配音后可用；任一场景缺配音置灰并提示；
  成功后提示 SRT 保存位置
- 生成期间忙碌状态；失败提供重试；错误不影响其他场景已生成结果
- UI 不直接调用 edge-tts、不计算时间轴、不读写 JSON、
  不直接修改 Project.output

### 8. app.py 注入

- 组装 EdgeTTSProvider、AudioProbe、AudioService、SubtitleService
  并注入；沿用既有 ConfigStore/TaskRunner

## 取消语义（软取消）

MVP 的"取消"定义为软取消：

- task_id 失效、晚到结果丢弃（复用 TaskRunner 语义）
- 临时文件清理，不得留下 .part
- 取消后不得写入 Scene 或 Project
- 不承诺立即中止底层网络连接；请求最迟在 timeout 后自行结束

## 断网行为定义

- 应用启动、项目管理、拆分、场景编辑、图片流程（缓存命中时）、
  已生成音频与字幕的查看在断网时完全不受影响
- 只有用户主动点击生成语音后才显示网络错误
- 错误后可重试；已完成场景的音频与时长保留
- 项目和已有数据不得被修改

## 本次不做

- FFmpeg、视频合成、字幕烧录（Phase 5）
- credits.txt（Phase 5）
- 逐字字幕、卡拉OK 字幕（不要求逐字时间戳）
- 字幕字号/位置设置（Phase 5 渲染时处理）
- 音频波形显示、应用内播放器（用户可用系统播放器打开 mp3）
- 更换 TTS 服务商、TTS API Key 体系
- 创建后修改语音/语速（后续阶段）
- 背景音乐、音效
- Phase 5 及后续功能

## 已确认默认规则（新增部分）

- 延续既有全部默认规则
- speech_rate 默认 "+0%"，仅三档合法，非法值安全回落并记录警告
- voice 存储 "female"/"male"；"default" 兼容为 female
- 音频缓存内容寻址：tts_<hash24>.mp3（SHA-256 前 24 位十六进制，
  基于 canonical JSON 缓存键）
- 缓存命中必须经 AudioProbe 再验证
- 字幕：cue ≤ 32 字、行 ≤ 16 字、≤ 2 行；长场景多 cue；
  时间一律整数毫秒
- SRT 时间格式 HH:MM:SS,mmm，UTF-8
- 新增依赖：edge-tts、mutagen（pyproject.toml）
- 测试一律使用 FakeTTSProvider / FakeAudioProbe，
  不发真实网络请求；真实 edge-tts 合成列入 macOS 人工验收

## 计划创建或修改的文件

新建：

- src/auto_video_maker/providers/tts_provider.py
- src/auto_video_maker/infrastructure/audio_probe.py
- src/auto_video_maker/services/audio_service.py
- src/auto_video_maker/services/subtitle_service.py
- tests/unit/test_tts_provider.py
- tests/unit/test_audio_probe.py
- tests/unit/test_audio_service.py
- tests/unit/test_subtitle_service.py
- tests/unit/test_scene_service_audio.py
- tests/integration/test_audio_subtitle_workflow.py

修改：

- pyproject.toml（+edge-tts、+mutagen）
- src/auto_video_maker/models/project.py
  （仅 ProjectSettings.speech_rate 及其验证/容错）
- src/auto_video_maker/services/scene_service.py
  （仅新增 set_scene_audio 与既有方法的失效规则扩展）
- src/auto_video_maker/services/project_manager.py
  （仅新增 set_subtitle_path / clear_subtitle_path）
- src/auto_video_maker/ui/new_project_dialog.py（语音/语速下拉）
- src/auto_video_maker/ui/scene_page.py（配音与字幕区域）
- src/auto_video_maker/app.py
- src/auto_video_maker/infrastructure/config.py（TTS 隐私三字段）
- tests/unit/test_ui_smoke.py、tests/unit/test_config.py、
  tests/unit/test_scene_service.py（失效规则回归）
- README.md（阶段状态与使用说明）

不修改：

- SceneSplitter/ImageProvider/SecretStore/LLMClient 等既有接口
- SceneService 其余既有公开行为
- docs/ 四份文档

## 测试要求（一律 Fake/Mock，不发真实网络请求）

模型与配置：
1. speech_rate：默认值、三档验证、旧项目缺字段回落、
   非法值安全回落且不传给 Provider
2. voice：female/male 存储、default 兼容映射
3. TTS 隐私三字段：确认后才调用、拒绝零请求零任务零临时文件、
   notice_version 或 provider 变化后重新确认、与 LLM 确认互不影响

缓存与音频：
4. 缓存键：provider_id/voice_id/rate/output_format/text 任一变化
   → hash 变化；场景重排不变化；hash 为 24 位十六进制
5. 缓存命中经 AudioProbe 再验证；验证失败视为未命中重新合成
6. AudioService：命中零合成；未命中合成并实测时长；
   合成失败 → 异常传播、无 .part 残留、Scene 不变
7. AudioProbe：正常读取、文件缺失/损坏/时长非正报错
8. 批量生成：逐场景写入、失败即停、已完成保留、进度回调、
   不自动保存、重试走缓存
9. 软取消：晚到音频结果不得写入 Scene（TaskRunner 语义）

失效规则（专项）：
10. 修改场景文字 → 该场景 audio_path/duration 清空 +
    subtitle_path 清空
11. 添加/删除/重排场景 → subtitle_path 清空
12. 重新生成任一场景音频 → subtitle_path 清空
13. 失效只清引用，不删除缓存文件

SceneService 扩展：
14. set_scene_audio：相对路径/防逃逸/duration>0 校验、失败零副作用、
    成功置 dirty
15. 项目状态 Service（ProjectManager）正确写入和清除 subtitle_path
    （相对路径校验、防逃逸）；SubtitleService 不直接修改 Project
    （专项断言）

字幕：
16. ≤32 字场景单 cue；>32 字场景多条连续 cue，拼接不变量成立
17. 标点优先分段；无标点硬切；每条 ≤32 字、≤2 行、每行 ≤16 字
18. 毫秒时间轴：无重叠、start<end、场景末 cue end_ms 等于场景结束、
    项目末 cue 不超过总时长（含比例分配舍入场景）
19. SRT 格式：序号、HH:MM:SS,mmm、UTF-8 中文、空行分隔
20. 缺 duration 场景 → 明确报错
21. subtitle_path 经 ProjectManager.set_subtitle_path 写入并随保存
    持久化，重开项目后 audio_path/duration/subtitle_path 完整

回归：
22. 向后兼容：Phase 3 项目正常加载与保存
23. 既有全部测试保持通过

## 完成标准

- 每个场景可生成中文配音，时长为 mp3 实测值
- 相同缓存键二次生成走缓存（零网络），且缓存经再验证
- 三条核心失效规则全部生效且有测试锁定
- 全部配音就绪后可生成同步 SRT，cue 划分与毫秒时间轴规则全部满足
- SubtitleService 不修改 Project；subtitle_path 写入只经
  ProjectManager（项目状态 Service）
- audio_path/subtitle_path 均为相对路径，project.json 无绝对路径
- 断网时既有功能完全不受影响；TTS 失败不崩溃、不波及其他场景
- 软取消语义生效：取消后零写入、零 .part 残留
- 全部自动测试通过且不访问真实网络
- README 已更新阶段状态

## macOS 人工验收流程

1. 新建项目（女声/正常语速），粘贴三段中文文案，拆分场景
2. 对第一个场景点「生成语音」→ 首次弹 TTS 隐私确认 → 确认后生成，
   显示时长；用系统播放器试听 audio/ 中的 mp3
3. 「生成全部语音」→ 进度显示 → 全部完成
4. 再次「生成全部语音」→ 秒完成（缓存命中，无网络等待）
5. 修改某场景文字 → 该场景立即回到"未生成"状态 → 重新生成 →
   新音频内容正确
6. 「生成字幕」→ 检查 subtitles.srt：中文正常、时间递增不重叠、
   长场景拆成多条 cue、每行 ≤16 字
7. 增删/移动一个场景 → 字幕状态失效 → 重新生成字幕
8. 保存并重开项目 → 音频路径、时长、字幕路径完整保留
9. 断网 → 点「生成语音」→ 友好网络错误；其余功能一切正常
10. 用男声新建另一项目，确认音色不同；语速选"快"确认语速变化

## 交付报告

Completed / Files created / Files changed / Baseline test result /
Tests executed / Final test result / How to run /
macOS manual verification steps / Known limitations /
Recommended next task

## 后续阶段

### Phase 5：视频生成

- FFmpeg 检测、图片转视频、图片裁剪
- 场景合并、添加语音、添加字幕（烧录）
- 生成 MP4、渲染进度、取消渲染
- credits.txt 随导出统一生成

### Phase 6：打包与发布

- 打包 FFmpeg、构建 macOS App、构建 DMG
- 在干净设备测试、编写安装说明、记录已知限制
- Windows 10 / Windows 11 打包放到后续阶段进行
