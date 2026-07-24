# Current Development Task

## 项目目标

构建 Auto Video Maker 的第一个可运行 MVP。

不要一次完成全部功能。

必须按照阶段逐步开发，每完成一个阶段后运行测试和更新任务状态。

## 当前阶段

Phase 3：图片素材系统。

阶段状态：

- Phase 1、Phase 2：已完成并通过 macOS 本机验收
- Phase 2.5: implementation complete and automated tests passed;
  live LLM provider integration test deferred.

## 阶段目标

为每个场景提供配图能力：生成搜索关键词、从开放许可来源搜索与下载图片、
支持本地图片替换，并完整保存素材版权信息。
断网或未配置 LLM 时，除图片搜索本身外的全部既有功能不受影响。

## 数据流与职责划分

```
Scene.text
  ↓
KeywordService（services/，本阶段新增）
  ├── LLM 关键词生成（可选，复用 LLMClient；隐私确认机制复用）
  └── 规则兜底：清理并截短场景文字生成简短搜索文本
  ↓ search_keywords（list[str]，用户可编辑，写入 Scene）
ImageProvider 接口（providers/，本阶段新增）
  └── OpenverseImageProvider（第一版唯一实现，匿名访问）
  ↓ list[ImageCandidate]（统一候选结构）
AssetDownloadService（services/，本阶段新增）
  → 下载（.part 临时文件）、校验、原子移动、缓存，写入项目 assets/
  ↓
SelectedAsset 模型（models/，唯一的资产验证/构造入口）
  ↓
SceneService.set_scene_asset（最小新增方法）
  → 将完整版权元数据写入 selected_asset，置 dirty
  ↓
ProjectManager 保存（复用）
```

职责边界：

- UI 不得直接调用 Openverse 接口，必须经 ImageProvider 接口
- 下载、校验、缓存只在 AssetDownloadService
- 资产数据必须经 SelectedAsset 模型这一单一验证/转换入口生成；
  UI 不得自行拼装资产字典
- Scene 的写入仍只经唯一 SceneService；本阶段允许新增最小方法：
  - set_scene_keywords(project, index, keywords: list[str])
  - set_scene_asset(project, index, asset: SelectedAsset)
  均需防御性验证，失败时项目状态不变；不得改变既有公开行为
- 网络操作一律经 TaskRunner 后台执行（复用），可取消，
  取消语义与 Phase 2.5 相同（task_id 失效、晚到结果丢弃）
- app.py 仍是唯一 composition root；__main__.py 不变

## 硬约束

- 图片只从开放许可或明确授权来源获取
- 不直接抓取 Google Images、Bing Images 或其他搜索引擎结果
- 不绕过网站 API、robots.txt 或访问限制
- 每张选中图片必须完整保存版权元数据（见 SelectedAsset 字段）
- selected_asset.local_path 必须是相对于项目根目录的路径
  （例如 assets/openverse_xxx.jpg）；
  不得把用户电脑的绝对路径写入 project.json
- local_path 不得包含路径逃逸：
  - 将 project_root / local_path 解析为规范路径后，
    结果必须仍位于 project_root 内
  - 拒绝绝对路径、`..` 路径逃逸以及指向项目目录外部的符号链接
  - 验证失败时不得修改 Scene 或项目状态
- 不下载可执行文件作为素材；文件名安全处理；限制文件大小与类型
- 所有网络请求设置 timeout、User-Agent、有限重试
- 图片搜索失败不得导致崩溃或丢失项目数据

## SelectedAsset 数据模型（models/selected_asset.py）

必须保存的字段：

```
provider          # 例如 "openverse" / "local"
source            # Openverse 返回的内容源（如 wikimedia、flickr）
asset_id
title
local_path        # 相对项目根目录，如 assets/openverse_xxx.jpg
source_page
author
author_url
license
license_version
license_url
attribution
width
height
```

- 优先使用 SelectedAsset dataclass，而不是在各处传递未定义的 dict
- Scene.selected_asset 在 project.json 中仍为 JSON 对象；
  序列化/反序列化与验证只经 SelectedAsset 单一入口
- 本地图片：provider="local"、source="local"、asset_id=文件内容哈希、
  title=原文件名、license="user-provided"，其余来源字段为空字符串

向后兼容（不得破坏已有项目）：

- selected_asset 允许为 null，旧项目中没有图片素材时必须正常加载
- Project/Scene 反序列化必须通过 SelectedAsset.from_dict
  或统一转换入口处理
- 已有 project.json 不得因为新增 SelectedAsset 模型而无法打开
- 不得要求用户手动修改旧项目文件

Openverse 响应字段映射：

```
source_page  = foreign_landing_url
preview_url  = thumbnail
download_url = url
```

（preview_url 与 download_url 属 ImageCandidate，用于搜索与下载过程；
SelectedAsset 保存最终落盘后的元数据。）

## 当前任务范围

### 1. KeywordService（services/keyword_service.py）

- generate_with_llm(scene_text) -> list[str]：
  复用 LLMClient 生成 1–3 个英文搜索关键词；
  可用性与隐私确认复用 Phase 2.5 机制
  （关键词生成同样把场景文字发送至模型服务，共用同一确认）
- generate_fallback(scene_text) -> list[str]：规则兜底，
  清理空白并生成简短搜索文本；不得无条件提交完整场景原文
- 最终提交给 Openverse 的 query 不得超过 200 个字符（统一截断保护）
- Phase 3 不得依赖真实 LLM 配置：未配置 LLM 时，
  用户仍可编辑关键词并正常搜索 Openverse
- 关键词在 UI 中永远允许手动编辑
- 不引入第三方翻译服务
- 不修改 Scene；关键词写入经 SceneService.set_scene_keywords

### 2. ImageProvider 接口与 OpenverseImageProvider（providers/）

- ImageProvider 抽象接口：
  search(query: str, per_page: int) -> list[ImageCandidate]
- OpenverseImageProvider：调用 Openverse 官方 API
  （https://api.openverse.org/v1/images/），匿名访问
- 默认只请求 cc0、pdm、by 许可结果；暂时排除 NC、ND、SA 类型
- 必须处理限流：429 按 Retry-After 或有限退避机制处理
- 重试规则：网络错误、429、可恢复的 5xx 有限重试；4xx 不重试
- 请求带 timeout、User-Agent
- 无结果返回空列表（不是异常）；网络失败抛出用户可理解异常
- 响应字段按上文映射到 ImageCandidate；license 相关字段保留原值
- httpx.MockTransport 可注入测试

### 3. AssetDownloadService（services/asset_download_service.py）

- download(candidate, project_root) -> SelectedAsset
- 大小限制：单图最多 15MB，下载中超限即中止
- 格式校验：仅接受 JPEG、PNG、WebP；
  不得只依赖 HTTP Content-Type，必须验证实际文件格式
  （魔数/Pillow 实际解码）
- 使用 .part 临时文件下载，全部验证通过后原子移动到 assets/
- Pillow 完整性校验，并处理 decompression bomb 风险
  （设置像素上限，超限视为校验失败）
- 重试规则：只对网络错误、429 和可恢复的 5xx 重试；
  格式错误、损坏图片、大小超限不得重试
- 缓存：按 provider + asset_id 生成安全文件名；
  缓存文件必须完整验证通过后才允许复用
- local_path 一律记录为相对项目根目录的路径
- 不执行、不解压任何下载内容

### 4. SceneService 最小扩展

- set_scene_keywords(project, index, keywords)：
  keywords 必须是 list[str] 且每项非空白
- set_scene_asset(project, index, asset)：
  asset 必须是有效的 SelectedAsset（经模型验证入口）
- 两者成功后置 dirty；验证失败时项目状态不变
- 不得改变既有公开行为

### 5. 本地图片上传

- 用户可为场景选择本地图片文件（JPEG/PNG/WebP）
- 复制进项目 assets/（不移动原文件），Pillow 校验（含 bomb 防护）
- 经 SelectedAsset 入口构造：provider="local"、
  asset_id=文件内容哈希、license="user-provided"（字段见模型节）

### 6. 场景页图片 UI

- 场景页每个场景显示配图状态（未配图 / 已配图）
- 「搜索图片」：用当前关键词经后台任务搜索 → 候选列表对话框
  （预览图 + 作者 + 许可证）→ 用户选择 → 后台下载 → 写入场景
- 「重新搜索」：可编辑关键词后再次搜索
- 「使用本地图片」：文件选择器上传
- 搜索与下载期间显示忙碌状态、可取消；失败提供重试
- 预览图加载失败不崩溃（显示占位）
- UI 不直接创建 Scene、不直接读写 JSON、不直接发网络请求、
  不自行拼装资产字典

### 7. app.py 注入

- 组装 KeywordService、OpenverseImageProvider、AssetDownloadService
  并注入场景页；沿用既有 ConfigStore/SecretStore/TaskRunner

## 断网行为定义

- 应用启动、项目管理、文案拆分、场景编辑在断网时完全不受影响
- 只有用户主动点击搜索/下载后才显示网络错误
- 错误后可重试或改用本地图片
- 项目和已有场景数据不得被修改

## 本次不做

- credits.txt 文件生成（Phase 5 导出时统一生成；
  本阶段只保证 SelectedAsset 元数据完整）
- Wikimedia Commons 独立 Provider（接口已预留）
- NC、ND、SA 许可类型的图片
- 第三方翻译服务
- 图片编辑、裁剪、滤镜
- TTS、字幕、FFmpeg、视频生成、打包
- Phase 4 及后续功能

## 已确认默认规则（新增部分）

- 延续既有全部默认规则
- 图片下载限制：≤15MB，仅 JPEG/PNG/WebP，实际格式校验
- Openverse 许可过滤：cc0、pdm、by
- Openverse query ≤ 200 字符
- 关键词双轨：LLM 可选 + 清理截短的规则兜底，永远可手动编辑
- local_path 相对项目根目录
- 测试一律使用 FakeImageProvider / httpx.MockTransport / FakeLLMClient，
  不发真实网络请求

## 计划创建或修改的文件

新建：

- src/auto_video_maker/models/selected_asset.py
- src/auto_video_maker/providers/image_provider.py
  （接口 + ImageCandidate + OpenverseImageProvider）
- src/auto_video_maker/services/keyword_service.py
- src/auto_video_maker/services/asset_download_service.py
- src/auto_video_maker/ui/image_search_dialog.py
- tests/unit/test_selected_asset.py
- tests/unit/test_image_provider.py
- tests/unit/test_keyword_service.py
- tests/unit/test_asset_download_service.py
- tests/unit/test_scene_service_assets.py
- tests/integration/test_image_workflow.py

修改：

- pyproject.toml（+Pillow）
- src/auto_video_maker/app.py
- src/auto_video_maker/services/scene_service.py（仅新增两个方法）
- src/auto_video_maker/ui/scene_page.py
- tests/unit/test_ui_smoke.py
- README.md

## 测试要求（一律 Fake/Mock，不发真实网络请求）

1. Openverse 响应正确映射为 ImageCandidate
   （source_page=foreign_landing_url、preview_url=thumbnail、
   download_url=url；license/license_version/license_url/attribution 保留）
2. 请求包含 cc0、pdm、by 许可过滤参数
3. 无结果返回空列表；网络错误分类；429 按 Retry-After 退避；
   4xx 不重试；重试次数有限
4. query 超过 200 字符被截断
5. 下载：实际格式校验（伪装 Content-Type 被拒绝）、大小超限中止、
   损坏图片拒绝、decompression bomb 拒绝、
   格式/损坏/超限不重试、网络错误重试
6. .part 临时下载与原子移动；失败后无残留生效文件
7. 缓存复用前完整验证；损坏缓存不复用
8. 关键词：LLM 成功生成、LLM 未配置/失败时兜底可用、
   兜底结果为清理截短文本而非完整原文（长文本场景）
9. SelectedAsset：字段完整性验证、缺字段拒绝、
   local_path 为相对路径（绝对路径被拒绝或转换）、
   序列化/反序列化 roundtrip
10. set_scene_keywords / set_scene_asset：防御性验证、
    失败时项目状态不变、成功置 dirty
11. 本地上传：复制、内容哈希、license="user-provided"、bomb 防护
12. 候选选择 → 下载 → 写入 → 保存 → 重开完整集成流程，
    重开后版权元数据完整、local_path 相对且可解析
13. 取消搜索/下载后晚到结果丢弃（复用 TaskRunner 语义）
14. 既有全部测试保持通过
15. 路径逃逸：set_scene_asset 拒绝 ../outside.jpg 等逃逸路径，
    且项目状态保持不变
16. 向后兼容：加载 selected_asset=null 的旧项目成功；
    保存后仍可重新打开

## 完成标准

- 每个场景可以：生成/编辑关键词 → 搜索候选 → 选择下载 → 或本地上传
- 选中图片的 SelectedAsset 全部字段完整保存并随 project.json 持久化
- local_path 为相对路径，project.json 中无用户绝对路径
- 未配置 LLM 时关键词编辑与 Openverse 搜索完全可用
- 断网时既有功能完全不受影响
- 全部自动测试通过（含既有测试），且不访问真实网络
- README 已更新阶段状态

## macOS 人工验收流程（自动测试之外，完成后必须提供）

1. 打开项目，选择一个场景，将关键词设为 "Sydney Opera House"
2. 点击搜索，显示候选缩略图
3. 候选中显示作者与许可证
4. 选择一张并下载成功
5. 保存项目并重新打开
6. 确认图片与版权信息仍然存在（selected_asset 字段完整、图片文件在 assets/）
7. 断网后再次搜索：显示失败提示，项目数据不丢失

## 交付报告

Completed / Files created / Files changed / Tests executed /
Test results / How to run / Manual verification / Known limitations /
Recommended next task

## 后续阶段

### Phase 4：语音和字幕

- TTS Provider Interface、中文语音（edge-tts）
- 音频缓存、音频时长
- 场景级字幕、SRT 输出

### Phase 5：视频生成

- FFmpeg 检测、图片转视频、图片裁剪
- 场景合并、添加语音、添加字幕
- 生成 MP4、渲染进度、取消渲染
- credits.txt 随导出统一生成

### Phase 6：打包与发布

- 打包 FFmpeg、构建 macOS App、构建 DMG
- 在干净设备测试、编写安装说明、记录已知限制
- Windows 10 / Windows 11 打包放到后续阶段进行
