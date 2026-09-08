# 快手 Web 视频统计工具：跨会话项目记忆

最后更新：2026-09-08（Asia/Tokyo）

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

下一步：先以匿名首屏 20 条完成 P2 批量轻量评论查询、阈值过滤与 XLSX 的端到端冒烟验证；需要采集多页时，再登录一个正常账号并验证 `pcursor=1` 的第二页加载。登录过程不启用探针。

最新身份状态：用户要求放弃刚才登录的账号身份。旧 `runtime/` 已完整删除，包括 Profile、Cookie、LocalStorage、缓存、历史和原始抓包；CloakBrowser 引擎缓存保留。程序已新增 `runtime/identity.json`，首次启动生成随机指纹种子并在同一身份生命周期内持久复用。当前为全新指纹和空白未登录 Profile。

全新身份匿名验证结果：未登录状态直接访问 `https://www.kuaishou.com/search/vpn` 正常，页面完整显示首屏 20 条；`POST /rest/v/search/feed` 返回 `result: 1`、`pcursor: "1"`，未出现 `risk-control` 或“网络异常”。因此旧异常与先前 Profile/账号会话状态相关，不是 CloakBrowser、CDP 探针或该搜索入口的固定故障。匿名状态仍受首屏 20 条和登录门槛限制。

本次检查点：

- 本地 Git 分支：`main`。
- 关键提交：`46c9c32`（网络探针/解析器/Web 面板）、`9797fb6`（持久化指纹与身份重置）、`33f133e`（匿名验证记录）。
- 专项测试：15 项通过。
- 本会话运行时：本地 Web 服务位于 `127.0.0.1:8080`；全新匿名浏览器当前打开 `/search/vpn`，探针已停止。进程状态属于临时状态，跨机器或进程重启后应以 `runtime/identity.json` 和 Profile 目录为准。

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

后续集成：正式采集任务默认读取 `建议快手采集 = TRUE` 的 1,087 个词；用户可按主题、搜索意图、优先级和平台敏感标记缩小范围。

TSV 集成决定：`resources/keywords/vpn_kuaishou_search_keywords.tsv` 是后续快手采集器的关键词输入文件。它包含全部 2,463 个提纯词，而不是只输出 1,774 个推荐子集；每行包含关键词、URL 编码词、完整 `https://www.kuaishou.com/search/{encoded_keyword}` 地址、主题、意图、优先级、平台敏感标记、推荐标记和来源数。文件为 UTF-8、LF、Tab 分隔，共 2,464 行（含表头），关键词无重复，473 个加速器词全部包含。用户样本“关键词”列的 16 个规范化唯一词均已覆盖。
