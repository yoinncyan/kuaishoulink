# 快手 Web 视频统计工具：跨会话项目记忆

最后更新：2026-09-07（Asia/Tokyo）

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

当前状态：正在建立本地 Git 仓库；尚未开始实现采集器或修改业务代码。

下一步（需要用户明确开始指令）：实现 P0 网络探针，启动 CloakBrowser，让用户在真实快手页面完成登录和一次关键词搜索，然后分析捕获结果。

## 9. 决策日志

- 2026-09-07：确认产品是快手 Web 统计专用程序，而不是外置采集脚本。
- 2026-09-07：确认核心数据必须来自加载接口和网络响应，DOM 仅用于触发页面行为。
- 2026-09-07：确认 SQLite 暂缓，先完成网络探测、数据解析和 XLSX 链路。
- 2026-09-07：确认先做 P0 网络探针，再根据真实快手响应开发解析器。
- 2026-09-07：确认最终产物为部署在服务器上的 Docker 服务，通过专用 Web 界面操作；不再以桌面 `.app/.dmg` 为交付目标。
