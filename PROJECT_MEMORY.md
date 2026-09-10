# 快手 Web 视频统计工具：跨会话项目记忆

最后更新：2026-09-10（Asia/Tokyo）

## 1. 产品目标

将 CloakBrowser 定制为一个单一用途的服务器 Web 程序：最终以 Docker 镜像部署，访问 Web 界面后只提供“快手 Web 视频搜索与统计”功能，不呈现通用浏览器 Profile 管理产品。

目标工作流：

1. 用户输入多个关键词，例如：跨境、VPN、网络加速、加速器、外贸。
2. 程序通过 CloakBrowser 打开快手 Web，并触发关键词搜索和滚动加载。
3. 从浏览器加载的网络请求与响应中提取视频数据。
4. 筛选评论数量大于用户阈值（默认 50）的视频。
5. 按视频唯一 ID 去重；同一视频命中多个关键词时合并关键词。
6. 导出视频链接、视频标题、作者、评论数量、命中关键词等字段到 XLSX。

## 2. 已确认的核心原则

### 网络响应驱动，而非 DOM 数据抓取

- 核心采集来源是快手页面实际加载的 Fetch/XHR、GraphQL、WebSocket 和其他网络响应。
- 标题、作者、视频 ID、评论数量等业务数据不以 DOM 文本为主要来源。
- DOM/页面操作只负责触发行为，例如登录、输入关键词、点击搜索和滚动加载。
- 优先监听 Playwright `request`/`response`/`websocket` 事件；需要更底层信息时使用 CDP `Network.*`。
- 如响应经过前端二次解码，可在页面脚本执行前安装 Fetch/XHR/WebSocket/Response Hook，捕获页面实际消费的数据，但仍不依赖 DOM 文本。

### 先探测协议，再开发正式采集器

第一阶段不是做数据库或完整 UI，而是实现 P0 网络探针，捕获一次真实快手搜索过程中的接口、请求参数和响应 Body，确认：

- 搜索结果接口及分页方式。
- GraphQL `operationName` 或其他稳定的接口指纹。
- 评论数量位于搜索响应、详情响应还是 WebSocket 数据中。
- 视频唯一 ID、标题、作者、链接和评论数量的真实字段路径。
- 返回格式是 JSON、二进制协议还是应用层编码数据。

在真实响应结构确认前，不臆测或硬编码具体快手接口名和字段名。

### SQLite 暂缓

- 用户明确提出当前先不考虑 SQLite。
- P0/P1 阶段使用内存数据结构和捕获文件即可。
- 原始探测数据建议保存为 JSONL、JSON 或二进制 Body 文件。
- 最终结果先直接生成 XLSX；持久化数据库留待后续确认。

## 3. 推荐技术架构

```text
专用 React Web 界面
        ↓
FastAPI 任务控制器
        ↓
CloakBrowser 持久化会话（Docker/Linux）
        ↓
Playwright + CDP 网络探测器
        ├── Fetch/XHR
        ├── GraphQL
        ├── WebSocket
        └── Response Body
        ↓
快手响应识别与解析器
        ↓
评论阈值过滤 + 视频 ID 去重
        ↓
XLSX 导出
```

计划复用 CloakBrowser Manager 的 Web、Docker 和远程浏览器查看能力：

- 后端：Python + FastAPI
- 前端：React
- 浏览器自动化：CloakBrowser + Playwright/CDP
- 登录交互：容器内虚拟显示器 + noVNC/KasmVNC，仅在登录或排障时显示
- 部署：Docker 镜像 + Docker Compose
- 数据目录：挂载持久化卷，保存快手 Profile、配置和导出文件
- 导出：openpyxl 或等价 XLSX 库

## 4. CloakBrowser 项目边界

- 当前项目根目录已拉取官方 `CloakHQ/CloakBrowser` 仓库内容，用于检查和后续集成；尚未进行业务代码修改。
- 官方 CloakBrowser Python/JS wrapper 是 MIT 开源。
- 官方 CloakBrowser Manager GUI 是 MIT 开源，已在临时目录中检查过其 FastAPI、React、Docker、noVNC/KasmVNC 架构，但尚未合并到当前项目。
- CloakBrowser 定制 Chromium 二进制使用独立许可证，不属于此仓库的完整开源源码。
- 产品应修改 wrapper/Manager 与业务逻辑，通过 Playwright/CDP 获取网络数据；无需修改 Chromium C++ 网络栈。
- 浏览器二进制优先在首次运行时从官方渠道下载，不直接嵌入对外分发的安装包。

## 5. 开发阶段

### P0：网络探针

目标：在 CloakBrowser 中手动完成一次快手关键词搜索，并完整记录相关网络流量。

应实现：

- 固定、持久化的快手浏览器 Profile。
- 页面导航前安装监听器。
- 捕获请求 URL、Method、资源类型、Headers、POST Body。
- 捕获响应状态、Content-Type 和完整响应 Body。
- 捕获 WebSocket 收发帧。
- 按搜索动作和关键词标记捕获会话。
- 生成请求列表、响应列表、Body 文件及汇总报告。
- 提供敏感 Header/Cookie 的默认脱敏机制。

验收标准：

- 能识别关键词搜索触发的主要接口。
- 能识别滚动加载产生的分页接口。
- 能读取或保存完整响应 Body。
- 能判断评论数量来自哪一类响应。

### P1：响应解析器

- 根据真实样本建立接口指纹：URL、Method、operationName、请求结构和响应结构。
- 从网络响应中提取视频 ID、标题、作者、链接和评论数量。
- 处理评论数单位，例如 `1.2万` 转换为 `12000`。
- 使用录制的脱敏响应 Fixture 做离线回归测试。

### P2：自动采集任务

- 多关键词顺序执行。
- 自动搜索和滚动加载。
- 连续多轮无新视频后停止当前关键词。
- 搜索响应不含评论数时，通过加载详情触发对应统计接口。
- 以视频 ID 去重，并合并命中关键词。
- 支持开始、暂停、停止和实时进度。

### P3：Docker Web 程序

- 删除通用 Profile 管理界面。
- 保留单一持久化快手登录会话。
- 提供关键词、评论阈值、最大采集数量等设置。
- 展示实时结果和统计信息。
- 一键导出 XLSX。
- 构建 Docker 镜像，并提供 Docker Compose 启动配置。
- 浏览器 Profile、任务配置和导出文件通过 Volume 跨容器重启保留。
- Web 界面提供登录/排障用的远程浏览器视图；日常采集由后台浏览器执行。
- Web 服务的监听地址、端口和访问认证通过环境变量配置。

## 6. 建议代码结构

```text
backend/
└── kuaishou/
    ├── browser_session.py
    ├── network_probe.py
    ├── cdp_monitor.py
    ├── websocket_monitor.py
    ├── endpoint_detector.py
    ├── response_parser.py
    ├── search_controller.py
    ├── task_runner.py
    └── exporter.py

captures/           # 本地原始接口样本，不默认提交敏感数据
parser-configs/     # 已确认的接口指纹和字段映射
tests/fixtures/     # 脱敏后的离线响应样本
```

## 7. 暂定结果字段

```text
video_id
video_url
title
author_name
author_id
comment_count
comment_count_raw
matched_keywords
source_operation
collected_at
```

字段以真实网络响应为准，P0 完成后再最终确定。

## 8. 当前状态与下一步

当前状态：P0 网络探针、P1 搜索/评论响应解析器与最小 Web 控制面已经实现，并完成真实快手链路验证。

已实现：

- CDP `Network.*` 请求、响应和完整 Body 捕获。
- Fetch/XHR、GraphQL operationName、WebSocket 帧记录。
- Header、Cookie、Token、签名查询参数的默认脱敏。
- 风控响应头 `Intercept-Result` 的识别与 Web 面板提示。
- 单一持久化快手 Profile；浏览器与探针生命周期分离。
- 快手登录入口、指定快手 URL 导航、重新加载、截图预览、探针启停 API。
- React P0 Web 控制面及生产构建。
- 相关离线单元/API 测试。
- `/rest/v/search/feed` 成功响应解析为结构化视频记录。
- GraphQL `commentListQuery` 的 `commentCountV2` 自动回填。
- 评论内容数组在保存响应前剔除，只保留总数和游标。
- 支持在快手页面上下文内直接轻量查询单个视频评论总数，无需打开详情页。

真实探测结论：

- 用户确认的搜索入口：`https://www.kuaishou.com/search/vpn`，通用形式为 `https://www.kuaishou.com/search/{URL编码关键词}`。
- 已确认搜索视频接口：`POST https://www.kuaishou.com/rest/v/search/feed`。
- 首次请求 Body 结构：`keyword`、`page`、`webPageArea`、`pcursor`。
- 首次未验证的 CloakBrowser Profile 曾收到 `Intercept-Result: risk-control;400002`，响应包含 `ANTICRAWL_COMMON` 安全验证地址；之后也出现 `risk-control;2`。该身份后来已按用户要求删除。
- `https://www.kuaishou.com/?isHome=1&source=SEARCH` 可以加载首页，但会显示拼图滑块安全验证。
- 首次未验证 Profile 的 `/rest/v/profile/get` 响应为 `result: 109`；该观察用于确认未登录判定。
- 使用最新版普通 Chrome 的全新临时 Profile 做对照，同样收到搜索失败响应 `result: 2` 和“操作太快了，请稍微休息一下”。用户日常 Edge Profile 正常，因此关键差异更可能是已建立的 Cookie、登录状态与会话信誉，而不是 CDP 监听本身。
- 当前使用 CloakBrowser wrapper 0.5.10、keyless Chromium 145；最新引擎需要 CloakBrowser 免费或付费 License Key，后续仍需做最新版对照。
- 用户手动完成安全验证后，同一持久化 Profile 的搜索接口成功返回 `result: 1`、首屏 20 条视频。
- 搜索响应视频结构已确认：`feeds[].photo.id/caption/viewCount/likeCount` 与 `feeds[].author.id/name`；`feeds[].comment.us_c` 只是当前用户是否评论，不是评论总数。
- 评论总数来自 `POST /graphql`、`operationName: commentListQuery`、响应路径 `data.visionCommentList.commentCountV2`。真实样本 `3xrkgjnv59hha7u` 返回 1220；`3x9su263zefax89` 隔日从 745 增长至 747。
- 已验证方案 B：在已通过验证的 CloakBrowser 页面上下文中使用原生 `fetch('/graphql')`，只请求 `commentCount/commentCountV2`，HTTP 200、无 `risk-control`，无需进入详情页或模拟动态签名。
- 快手搜索页面左侧为导航栏，右侧大区域为搜索内容；滚轮必须发送到右侧内容区。自动滚动坐标当前按视口 `(72%, 72%)` 计算并分段发送可信 Wheel 事件。
- 匿名 Profile 下 `/rest/v/profile/get` 返回 `result: 109`。正确滚动到右侧内容底部后显示“想看更多视频，快去登录吧～”；匿名状态仅能使用首屏 20 条，继续分页需要登录。

下一步：从 `runtime/sampling/20260908-133841` 恢复匿名交错采样；全部链接完成后，再执行去重、字段补齐和评论数量阶段。需要采集多页时，再登录一个正常账号并验证 `pcursor=1` 的第二页加载。登录过程不启用探针。

最新身份状态：用户要求放弃刚才登录的账号身份。旧 `runtime/` 已完整删除，包括 Profile、Cookie、LocalStorage、缓存、历史和原始抓包；CloakBrowser 引擎缓存保留。程序已新增 `runtime/identity.json`，首次启动生成随机指纹种子并在同一身份生命周期内持久复用。当前为全新指纹和空白未登录 Profile。

历史匿名验证结果：全新身份曾经可以直接访问 `/search/vpn` 并返回 `result: 1`，但后续对照确认这种直达方式不稳定，同一新会话可能返回 `risk-control;2/400002`。当前已用更稳定的首页真实搜索交互取代直达 URL：从 `/?isHome=1&source=SEARCH` 的搜索框输入关键词并点击“搜索”，捕获新标签页及其接口响应。匿名状态仍受首屏 20 条和登录门槛限制。

本次检查点：

- 本地 Git 分支：`main`。
- 关键历史提交：`46c9c32`（网络探针/解析器/Web 面板）、`9797fb6`（持久化指纹与身份重置）、`33f133e`（匿名验证记录）。首页搜索交互、交错调度、后台模式、累计主记录及最新结果目前仍在工作区，尚未提交。
- 最新专项测试：37 项通过；前端 TypeScript/Vite 生产构建通过；`git diff --check` 通过。
- 本地 Web 服务当前运行在 `127.0.0.1:8080`，已加载“登录后全量滚动”开关；持久化 Profile 已登录。历史匿名模式独立检查点暂停在 591/49,260，本模式去重 1,836 条；累计主记录 1,948 条。登录态正式全量任务尚未由用户启动，临时端到端验证使用 `/tmp` 独立主表，未改动正式累计数据。
- 用户原文件 `快手视频链接.xlsx` 保持未修改、未纳入 Git。
- 恢复时先运行：

```bash
KUAISHOU_BROWSER_HEADLESS=true \
  .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8080

.venv/bin/python scripts/sample_kuaishou_top_keywords.py \
  --resume-dir runtime/sampling/20260908-133841 \
  --min-interval 6 --max-interval 12 --max-consecutive 5
```

## 9. 决策日志

- 2026-09-07：确认产品是快手 Web 统计专用程序，而不是外置采集脚本。
- 2026-09-07：确认核心数据必须来自加载接口和网络响应，DOM 仅用于触发页面行为。
- 2026-09-07：确认 SQLite 暂缓，先完成网络探测、数据解析和 XLSX 链路。
- 2026-09-07：确认先做 P0 网络探针，再根据真实快手响应开发解析器。
- 2026-09-07：确认最终产物为部署在服务器上的 Docker 服务，通过专用 Web 界面操作；不再以桌面 `.app/.dmg` 为交付目标。
- 2026-09-07：确认搜索入口为 `https://www.kuaishou.com/search/{keyword}`，并通过真实 CDP 捕获定位到 `POST /rest/v/search/feed`。
- 2026-09-07：确认全新 CloakBrowser 和普通 Chrome Profile 均可能触发快手反爬安全验证；产品必须保留一个可人工完成验证/登录的持久化 Profile。
- 2026-09-08：确认搜索列表不含评论总数；评论总量字段为 GraphQL `commentListQuery → data.visionCommentList.commentCountV2`。
- 2026-09-08：确认浏览器页面上下文可直接执行轻量 `commentListQuery`，最终批量方案无需逐个加载视频详情。
- 2026-09-08：确认匿名搜索仅显示首屏 20 条；要采集大量结果，持久化 Profile 必须先登录快手。
- 2026-09-08：按用户要求清除旧账号的完整本地浏览身份与原始抓包，生成新的持久化指纹种子和空白 Profile。
- 2026-09-08：确认重置后的全新指纹/Profile 在未登录状态下可正常搜索 `vpn` 并取得 20 条视频，无风控响应。
- 2026-09-08：确认使用 `快手视频链接.xlsx` 的真实标题和关键词反向扩展 SEO 词库；原文件保持不变，派生词进入统一 TSV。
- 2026-09-08：确认全新浏览器直接 `goto /search/{keyword}` 更容易触发 `risk-control`；正式搜索必须先打开 `/?isHome=1&source=SEARCH`，在真实搜索框输入关键词并点击“搜索”，再捕获新打开的搜索标签页。普通浏览器与 CloakBrowser 均已用“炸鸡”验证该路径返回 `result: 1`、20 条视频且无风控。
- 2026-09-08：匿名批量调度最初使用随机 5–10 秒；用户随后明确调整为随机 6–12 秒。每个关键词累计搜索 20 次；同一关键词每批只连续搜索随机 1–5 次，然后切换到下一个未完成关键词。
- 2026-09-08：自动采集默认改为 `KUAISHOU_BROWSER_HEADLESS=true` 的后台无窗口模式，避免 macOS 上 CloakBrowser 新开搜索标签时反复抢占前台焦点；人工登录/验证码排障时才显式使用 `KUAISHOU_BROWSER_HEADLESS=false`。已在无窗口模式通过首页搜索框实测“炸鸡”：`result: 1`、20 条视频、无 `risk-control`。
- 2026-09-08：用户要求采集控制不再依赖对话或终端。Web 首页已新增“开始/继续采集”“暂停并保存”“深度重置身份”按钮、6–12 秒与连续次数配置，以及总进度、当前关键词、本轮/累计去重统计。FastAPI 新增持久化采样子进程控制器和 `/api/task/*` 接口；服务重启后会自动发现最近的未完成检查点。
- 2026-09-08：用户进一步要求提供“打开 CloakBrowser 窗口”开关并默认开启。该开关已加入批量控制区：开启时，点击开始/继续会动态切换为可见窗口；关闭时使用后台无窗口模式。它覆盖任务启动时的服务初始模式，切换模式会安全关闭旧浏览器上下文并用同一持久化 Profile 重新启动。
- 2026-09-08：新增“自动深度重置”开关，默认关闭。关闭时，只有已确认进入搜索页后的真实接口异常才会保存检查点并将任务置为暂停，页面提示用户手动点击“深度重置身份”；开启后才自动深度重置并继续。手动重置会在当前关键词检查点中写入 `identity_reset` 确认，使下次继续不会重复要求重置。
- 2026-09-08：修复一次历史异常误报。`ipsec vpn` 第 6 次搜索曾在 15:48:25 触发风控，并已于 15:48:30 自动完成深度重置；旧逻辑随后用 `batch_complete` 覆盖了最后事件，恢复时误判为“尚未处理”。现改为在失败轮次本身持久化 `identity_reset_handled`，并排除不计轮次的导航错误；该误报已从当前检查点清除。
- 2026-09-08：再次修正异常分类。`vpn协议` 第 13 次尝试只是等待 `/rest/v/search/feed` 超时：页面已打开，但没有收到成功响应、失败响应或 `risk-control`，因此用户看不到页面异常。现将“无接口响应超时”标记为 `search_retry_without_reset`，不计入每词 20 次、不触发自动/手动重置提示，继续后自动重试；只有明确收到失败业务响应或风控头才显示“真实搜索异常”。
- 2026-09-08：第一阶段完成后启动第二阶段。累计主记录共 738 个唯一视频；评论总数通过页面上下文中的 GraphQL `commentListQuery` 逐条查询，不打开详情页、不读取评论内容，严格筛选 `comment_count > 50`，最终只输出 Markdown。
- 2026-09-08：应用户要求，将第二阶段最终 Markdown 转为 XLSX：`outputs/kuaishou-anonymous-sample-20260908/快手视频评论数大于50_最终结果.xlsx`。工作簿保留原 10 个字段，共 611 条去重合格视频，并已完成重新导入、行尾抽检、公式错误扫描与渲染校验。
- 2026-09-08：第一阶段扩展采集暂停在 411/49,260 后，第二阶段按累计主记录增量续跑，数据源从 738 条扩至 1,628 条；旧 738 条查询结果保留，只处理新增视频。完成后 1,627 条取得整数评论总数，1,316 条严格大于 50，311 条不大于 50，1 条总数不可用，待处理/错误为 0。
- 2026-09-08：将最新第二阶段 Markdown 同步导出为 XLSX 和 UTF-8 TSV，均保留 10 个字段与 1,316 条唯一视频记录；XLSX 已完成保存后重新导入、首尾抽检、错误扫描和渲染校验。
- 2026-09-08：第一阶段从 411/49,260 恢复时连续三次以退出码 1 结束。日志确认发生在搜索前的 `/api/browser/login-status`：管理器仍把已关闭/失效的 Playwright 页面标记为 running，页面求值返回 HTTP 500；这不是快手 `risk-control`，411 次有效搜索、1,514 条本轮去重链接和 1,628 条累计主记录均未丢失。现已增加页面存活检测、保留同一指纹/Profile 的浏览器进程自动重启与登录状态重试；未执行深度身份重置。控制器也会显示检查点中的真实异常，而不再只显示“退出码：1”，并在启动前异常时保留既有检查点位置。相关专项测试 34 项通过。Web 服务已用修复版重启，并以同一持久化 Profile 实测首页加载成功、`checkLoginQuery` HTTP 200、匿名状态正常且无 `Intercept-Result`。
- 2026-09-10：用户建立远程仓库 `https://github.com/yoinncyan/kuaishoulink.git`。远程代码提交默认包含程序、测试、关键词 TSV、文档和项目记忆；本地浏览器运行目录、原始 `快手视频链接.xlsx` 与 `outputs/kuaishou-anonymous-sample-*` 采集结果不进入 Git，避免把 Profile/运行状态和大批量采集数据发布到代码仓库。
- 2026-09-10：用户已在当前持久化 CloakBrowser Profile 登录快手，`checkLoginQuery` 实测为 `true`。新增“登录后全量滚动”模式并在 Web 控制台默认开启：每个关键词只通过首页搜索框提交 1 次真实搜索，之后在右侧结果区按 1.5–3 秒随机间隔持续下滑；以网络响应 `pcursor=no_more` 和页面可见“没有更多了”作为完成信号。DOM 只用于滚动终点控制，视频 ID、标题、作者和链接仍全部来自 `/rest/v/search/feed` 响应。
- 2026-09-10：登录态模式与匿名 20 轮模式使用独立运行目录、关键词完成状态和检查点；登录态的总目标固定为 2,463 个关键词 × 1 次，不显示或使用“同词连续上限”和可编辑“每词总次数”。两种模式继续写入同一个 `runtime/sampling/master/deduped_links.json` 和累计 Markdown，按视频 ID/链接与历史记录全局去重；各自运行目录单独保留本模式命中过的关键词。登录模式异常只暂停并保存，不自动深度重置已登录 Profile，Web 中深度重置按钮在该模式下禁用。
- 2026-09-10：真实协议验证“vpn安全吗”：一次首页搜索后滚动触发 16 个成功分页响应，原始/唯一视频均为 298，最终 `pcursor=no_more`、页面显示“没有更多了”，无风控。发现新标签页首屏请求存在 CDP 挂载竞态后，改为让真实搜索动作在预先挂载 CDP 的首页标签内完成跳转；独立端到端验证“虚拟专用网络”一次搜索、15 次下滑、16 个分页响应，取得 291 条唯一视频，终点游标和页面文字同时确认，账号仍保持登录且无 `Intercept-Result`。
- 2026-09-08：Web 控制台新增独立“第二阶段 · 评论数量”面板，提供开始/继续、暂停并保存、进度条、已查询/符合/不符合计数、最近错误、检查点路径和当前 Markdown 链接。第二阶段每条结果原子写入检查点，服务或任务重启后可继续；任务状态接口只返回紧凑摘要，不再每 1.5 秒传输完整结果字典。
- 2026-09-08：用户要求把第一阶段从前 10 个关键词扩展为 TSV 全部 2,463 个关键词，同时保留前 10 词的完成进度。范围已扩展为每词 20 次、总计 49,260 次；前 10 词的 200 次进度和 599 条本轮去重链接完整保留，下一关键词为第 11 条“vpn原理”。新关键词检查点文件名使用安全 slug 加 SHA-1 短摘要，避免全量词库中的 `/`、`?` 等字符形成非法路径。

## 10. VPN SEO 关键词词库

用户新增需求：从 SEO 关键词平台和关联词库寻找 VPN 相关关键词及长尾词，下载后提纯，作为后续快手搜索任务的候选词库。

已完成：

- 词源：Google Web 联想、Google/YouTube 联想、Bing 中文联想、百度联想、5118 公开长尾词索引。
- 初始种子范围：VPN 核心、VPN 产品/协议、企业远程办公、跨境电商、外贸网络、海外专线、代理 IP、防关联、异地组网、网络加速和游戏加速等 58 个种子，并对 40 个高关联结果做二级扩展。
- 用户提供 `快手视频链接.xlsx`，含 65 条真实快手视频记录。已读取但不修改原文件，从“关键词”列、标题和话题中提取 81 个高价值新词根；创作者名称、活动推广标签和泛娱乐标签未作为种子。
- 新增词簇包括：翻墙/外网/梯子/科学上网、VPN 法律/原理/速度/节点问题、TikTok/Telegram/Shadowrocket 连接问题、国际服/网络延迟/校园网/随身 WiFi、加速器测评/排行，以及迅游、雷神、奇游、TT 和具体游戏加速器词。
- 总种子数：139 个。
- 原始建议记录：3,790 条。
- 规范化后的提纯唯一词：2,463 条，比上一版净增 926 条。
- 建议优先用于快手采集：1,774 条。
- 长尾词：2,220 条。
- 剔除噪声：250 条，保留剔除原因供复核。
- 用户明确要求“加速器”相关词全部保留；当前保留 473 条，剔除 0 条。游戏加速器、手游加速器、回国加速器、海外加速器、品牌加速器及其下载/推荐/配置词只分类标记，不因泛化或游戏属性删除。
- 未伪造搜索量。公开联想源不提供统一搜索量，使用来源数、源内排名和可解释的规则相关度进行筛选，并在工作簿中明确说明这些字段不等于搜索量。

资产位置：

```text
research/seo/vpn-20260908/vpn_keywords_raw.csv
research/seo/vpn-20260908/vpn_keywords_refined.csv
research/seo/vpn-20260908/vpn_keywords_excluded.csv
research/seo/vpn-20260908/vpn_keywords_dataset.json
outputs/vpn-seo-keywords-20260908/vpn_seo_keywords_20260908.xlsx
resources/keywords/vpn_kuaishou_search_keywords.tsv
```

工作簿包含 `提纯词库`、`原始词库`、`剔除记录` 三张工作表，已经通过重新导入、关键区域检查、公式错误扫描、三张表视觉渲染及 XLSX 压缩结构校验。

后续集成：正式采集任务默认读取 `建议快手采集 = TRUE` 的 1,774 个词；用户可按主题、搜索意图、优先级和平台敏感标记缩小范围。

TSV 集成决定：`resources/keywords/vpn_kuaishou_search_keywords.tsv` 是后续快手采集器的关键词输入文件。它包含全部 2,463 个提纯词，而不是只输出 1,774 个推荐子集；每行包含关键词、URL 编码词、完整 `https://www.kuaishou.com/search/{encoded_keyword}` 地址、主题、意图、优先级、平台敏感标记、推荐标记和来源数。文件为 UTF-8、LF、Tab 分隔，共 2,464 行（含表头），关键词无重复，473 个加速器词全部包含。用户样本“关键词”列的 16 个规范化唯一词均已覆盖。

## 11. 前 10 关键词匿名交错采样检查点

当前正式采样采用全新运行目录，旧的连续采样结果只作诊断记录，不混入本轮：

```text
runtime/sampling/20260908-133841
outputs/kuaishou-anonymous-sample-20260908/前10关键词_每词20次_随机批次1至5_去重结果.md
runtime/sampling/master/deduped_links.json
outputs/kuaishou-anonymous-sample-20260908/快手视频链接_累计去重总表.md
```

第一阶段完成状态（2026-09-08）：

- 第一阶段搜索任务和网络探针已停止，持久化 Profile 保留。
- 本轮去重链接：599 条；累计主记录：738 条。
- 10 个关键词均完成 20 次有效搜索，总计 200/200；成功响应 195 次、明确失败响应 5 次，导航/无响应诊断失败不计入 200 次。
- 首批 28 次使用旧的随机 5–10 秒间隔，后续搜索使用新确认的随机 6–12 秒间隔。
- `progress.json` 已标记 `phase: completed`、`event: run_complete`。

全量扩展后的待启动状态：

- `resources/keywords/vpn_kuaishou_search_keywords.tsv` 共 2,463 个关键词，全部纳入任务。
- 每词 20 次，总目标 49,260 次；已保留前 10 词的 200 次，Web 总进度显示 200/49,260。
- `runtime/sampling/20260908-133841/progress.json` 当前为 `phase: paused`、`event: scope_expanded_all_keywords`；`scope.json` 记录扩展范围。
- 用户明确要求暂不启动，待第二阶段完成后由用户自行在 Web 控制台点击“继续全部关键词采集”。第二阶段现已完成，但全量第一阶段仍保持未启动。

累计主记录：

- 用户要求把旧批次 481 条与当前批次 189 条合并，二者重叠 100 条；累计去重后为 570 条。
- `runtime/sampling/master/deduped_links.json` 是机器可恢复的累计主数据，按 `video_id` 和 `video_url` 去重并合并命中关键词。
- `outputs/kuaishou-anonymous-sample-20260908/快手视频链接_累计去重总表.md` 是面向用户的唯一累计 Markdown；字段与 `快手视频链接.xlsx` 一致。
- 采样器后续每次成功搜索、错误检查点、批次完成或关键词完成时都会将新视频幂等合并进上述主 JSON，并原子更新同一份累计 Markdown；各运行目录仍作为独立审计记录保留。
- Web 任务控制器已经完成第一阶段的开始、暂停、恢复直至完成；累计总表已增长到 738 条。

## 12. 第二阶段：评论数量与最终 Markdown

固定资产：

```text
runtime/comments/master-comment-counts.json
outputs/kuaishou-anonymous-sample-20260908/快手视频评论数大于50_最终结果.md
```

实现与状态：

- 数据源固定为累计主记录中的 738 个唯一 `video_id`/链接。
- 每条查询后立即保存 `comment_count`、尝试次数、HTTP 状态、风控头、错误和查询时间；Ctrl+C、Web“暂停”或服务关闭均走保存流程。
- 单条请求带 15 秒 AbortController/服务端超时；失败项最多尝试 3 次。`risk-control` 会保存并暂停。
- 最终 Markdown 只列出评论数量严格大于 50 的视频，同时在顶部报告已完成、未完成、符合与不符合数量。
- Web 控制台可直接开始/继续或暂停第二阶段，并实时显示进度与最近 12 条错误；可通过 `/api/task/comment-output` 查看当前 Markdown。
- 面板升级时曾在 362/738 处优雅暂停，随后从检查点恢复并完成。
- 最终状态：737 条取得整数评论总数，其中 611 条严格大于 50、126 条不大于 50；另 1 条 `3xuwcjisjhjt48e` 收到 HTTP 200 的有效 GraphQL 响应，但 `commentCount/commentCountV2` 均为 `null`，标记为 `unavailable_null_count`。待处理/错误为 0。
- 最终 Markdown 已生成 611 条数据行：`outputs/kuaishou-anonymous-sample-20260908/快手视频评论数大于50_最终结果.md`。
- 后续增量续跑完成状态：数据源 1,628 条，评论数已取得 1,627 条，严格大于 50 的视频 1,316 条，不大于 50 的视频 311 条，评论总数不可用 1 条，待处理/错误 0 条。
- 最新 Markdown、XLSX、TSV 使用相同基础文件名，位于 `outputs/kuaishou-anonymous-sample-20260908/`；XLSX 与 TSV 均包含 1,316 条唯一视频记录和原始 10 个字段。
