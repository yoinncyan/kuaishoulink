# 快手 Web 视频统计工具

基于 CloakBrowser 的快手 Web 网络响应采集与统计服务。最终形态为部署在服务器上的 Docker 服务，通过专用 Web 界面完成登录、关键词任务、实时统计和 XLSX 导出。

## 当前阶段：P0 网络探针

当前版本用于确认快手 Web 的真实数据接口，不从 DOM 文本提取视频业务数据。

已经具备：

- 单一持久化 CloakBrowser Profile；登录 Cookie 跨重启保留。
- 从快手首页搜索框输入关键词并点击“搜索”，不直接打开搜索 URL。
- CDP `Network.*` 请求、响应及 Response Body 捕获。
- Fetch/XHR、GraphQL operationName 和 WebSocket 帧记录。
- 敏感 Header、Cookie、Token 和签名参数脱敏。
- 快手 `Intercept-Result: risk-control;*` 风控响应识别。
- `/rest/v/search/feed` 视频结果解析，以及 `commentListQuery.commentCountV2` 评论总数回填。
- 在快手页面上下文中直接轻量查询评论总数，无需逐个打开详情页。
- 浏览器与探针独立启停。
- React Web 控制面、浏览器截图预览和历史捕获会话列表。

真实探测已定位到搜索视频接口：

```text
POST https://www.kuaishou.com/rest/v/search/feed
```

请求 Body 当前观察为：

```json
{
  "keyword": "vpn",
  "page": "search",
  "webPageArea": "",
  "pcursor": ""
}
```

字段与协议以实际捕获为准，不直接调用或模拟该接口；程序让快手页面正常发起请求，并读取浏览器收到的响应。

当前确认搜索列表不包含评论总数。评论总数来自：

```text
POST /graphql
operationName: commentListQuery
data.visionCommentList.commentCountV2
```

已验证可在同一个已通过安全验证的 CloakBrowser 页面上下文中直接执行该轻量查询，不需要为每条视频加载详情页面。匿名搜索首屏为 20 条，继续加载更多结果需要先登录快手。

## 本地开发

要求：Python 3.9+、Node.js 18+。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]' -r backend/requirements.txt

cd frontend
npm install
npm run build
cd ..

KUAISHOU_BROWSER_HEADLESS=true \
  .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8080
```

访问：<http://127.0.0.1:8080>

推荐操作顺序：

1. 点击“打开快手登录页”。
2. 在 CloakBrowser 窗口中手动完成安全验证和登录。
3. 回到 Web 面板输入关键词。
4. 点击“启动探针”。
5. 在快手页面滚动，触发后续分页接口。
6. 点击“停止并保存”。

服务进程默认具备后台无窗口能力。Web 控制区中的“打开 CloakBrowser 窗口”开关默认开启，因此点击“开始/继续采集”时会显示窗口；需要边采集边使用电脑时关闭该开关即可。也可通过环境变量固定服务初始模式：

```bash
KUAISHOU_BROWSER_HEADLESS=false \
  .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8080
```

匿名交错采样默认每次间隔随机 6–12 秒，同一关键词每批随机连续 1–5 次，每个关键词累计 20 次。所有运行的新链接会持续合并到同一累计记录：

```text
runtime/sampling/master/deduped_links.json
outputs/kuaishou-anonymous-sample-20260908/快手视频链接_累计去重总表.md
```

登录后可启用 **“登录后全量滚动”** 模式。该模式与匿名多轮模式使用独立运行目录、关键词完成进度和检查点；每个关键词固定只提交 1 次首页搜索，然后按 1.5–3 秒随机间隔在右侧结果区持续下滑。程序同时检查网络响应游标 `pcursor=no_more` 和页面可见文字“没有更多了”，到达终点后把该关键词全部 `/rest/v/search/feed` 视频一次性合并进上述同一累计主记录，并按视频 ID/链接与历史数据去重。登录模式异常时只暂停并保留账号、Cookie、指纹和已有链接，不执行自动深度重置。

2026-09-10 的真实登录态验证：关键词“vpn安全吗”一次搜索后获得 16 个分页响应、298 条唯一视频；独立端到端回归以“虚拟专用网络”获得 16 个分页响应、291 条唯一视频。两次均以 `pcursor=no_more` 和可见“没有更多了”结束，无风控响应。

当前第一阶段范围已扩展为 TSV 全部 2,463 个关键词，总目标 49,260 次；前 10 个关键词已经完成的 200 次会从原检查点继续保留。Web 控制台显示“全部 2463 条”，用户点击“继续全部关键词采集”后才会启动剩余范围。

Web 首页提供独立的批量任务控制区，无需再通过终端或对话操作：

- **开始采集 / 继续采集**：自动选择最近的未完成检查点。
- **登录后全量滚动**：默认开启；使用独立关键词进度，每词固定搜索 1 次并滚动到“没有更多了”，但链接仍写入同一累计去重总表。
- **暂停并保存**：向采样进程发送优雅暂停信号，保存关键词次数、去重链接和累计总表。
- **深度重置身份**：仅用于匿名模式且任务暂停时；登录模式下禁用，避免删除已登录 Profile。
- **打开 CloakBrowser 窗口**：默认开启；关闭后任务在后台无窗口运行，不抢占桌面焦点。
- **自动深度重置**：默认关闭；关闭时遇到真实搜索异常会保存并暂停，等待用户决定是否点击手动重置；开启后才会自动重置并续跑。
- 页面实时显示独立模式总进度、当前关键词、当前词进度、当前词分页响应、当前词去重链接、本模式去重数和累计去重数。
- 第一阶段完成后，“第二阶段 · 评论数量”面板可逐条查询 `commentListQuery`，实时显示完成比例、评论数大于/不大于 50 的数量、未解决错误及检查点位置，并支持独立开始/继续和暂停保存。

对应接口为 `GET /api/task/status`、`POST /api/task/start`、`POST /api/task/pause` 和 `POST /api/task/reset-identity`。`GET /api/browser/search-page-state` 只读取“没有更多了”等滚动控制状态；视频标题、作者和链接仍全部来自网络响应。

第二阶段检查点和最终 Markdown：

```text
runtime/comments/master-comment-counts.json
outputs/kuaishou-anonymous-sample-20260908/快手视频评论数大于50_最终结果.md
```

捕获文件默认保存到：

```text
runtime/captures/<会话 ID>/
├── manifest.json
├── events.jsonl
├── summary.json
└── bodies/
```

`runtime/` 已加入 `.gitignore`，避免将登录 Profile 或真实响应数据提交到仓库。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `KUAISHOU_DATA_DIR` | `./runtime` | Profile 和捕获数据根目录 |
| `KUAISHOU_BROWSER_HEADLESS` | `true` | 自动采集使用后台无窗口模式；设为 `false` 才显示人工操作窗口 |
| `KUAISHOU_BROWSER_LOCALE` | 原生值 | 可选语言覆盖 |
| `KUAISHOU_BROWSER_TIMEZONE` | 原生值 | 可选时区覆盖 |
| `KUAISHOU_BROWSER_GEOIP` | 配置代理时开启 | 根据出口 IP 对齐语言和时区 |
| `KUAISHOU_PROXY` | 空 | 可选浏览器代理 |
| `KUAISHOU_PROBE_MAX_BODY_BYTES` | `26214400` | 单响应 Body 最大保存字节数 |
| `CLOAKBROWSER_LICENSE_KEY` | 空 | CloakBrowser License Key |
| `CLOAKBROWSER_RELEASE_CHANNEL` | 空 | Stable/Preview 发布通道 |

## 测试

P0 测试使用模拟 CDP 会话和脱敏响应，不访问快手：

```bash
.venv/bin/pytest -q \
  tests/test_kuaishou_network_probe.py \
  tests/test_kuaishou_browser_session.py \
  tests/test_kuaishou_api.py
```

## 后续阶段

- P1：根据成功搜索响应实现视频字段解析器与录制 Fixture 回归。
- P2：多关键词、自动滚动、评论阈值过滤、视频 ID 去重与 XLSX。
- P3：noVNC 登录视图、访问认证、Docker 镜像与 Compose 部署。

## VPN SEO 关键词词库

已从 Google Web、Google/YouTube、Bing 中文、百度搜索联想及 5118 公开索引采集 VPN、跨境网络、外贸网络、代理 IP、企业组网和加速器相关词，并完成规范化、去重、分类和噪声剔除。

```text
research/seo/vpn-20260908/
├── vpn_keywords_raw.csv
├── vpn_keywords_refined.csv
├── vpn_keywords_excluded.csv
├── vpn_keywords_dataset.json
└── errors.json

outputs/vpn-seo-keywords-20260908/
└── vpn_seo_keywords_20260908.xlsx

resources/keywords/
└── vpn_kuaishou_search_keywords.tsv
```

结合用户提供的 `快手视频链接.xlsx` 中 65 条真实视频样本反向扩词后，当前结果为：3,790 条原始记录、2,463 个提纯唯一词、1,774 个建议优先采集词、2,220 个长尾词。用户明确要求所有“加速器”相关词保留，当前共 473 个，剔除表中为 0 个。

`vpn_kuaishou_search_keywords.tsv` 包含全部 2,463 个提纯词。每行提供 `keyword`、URL 编码后的 `encoded_keyword` 以及可直接导航的：

```text
https://www.kuaishou.com/search/{encoded_keyword}
```

TSV 同时保留主题、意图、优先级、平台敏感标记和 `recommended_for_kuaishou`，后续采集调度器直接读取此文件。

重新抓取：

```bash
.venv/bin/python scripts/collect_vpn_seo_keywords.py \
  --output-dir research/seo/vpn-20260908 \
  --expand-limit 40
```

仅调整清洗规则后重新提纯，不访问外部词源：

```bash
.venv/bin/python scripts/collect_vpn_seo_keywords.py \
  --output-dir research/seo/vpn-20260908 \
  --refine-only
```

来源联想接口不提供可靠的统一搜索量，因此词库不虚构搜索量；`规则相关度`、`来源数`和`最佳来源排名`用于筛选，不等同于搜索量。

## 上游与许可证

本仓库基于 [CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser) 的 MIT wrapper 源码开发；原始上游说明保存在 [`UPSTREAM_README.md`](UPSTREAM_README.md)。CloakBrowser Chromium 二进制适用独立的 [`BINARY-LICENSE.md`](BINARY-LICENSE.md)，最终镜像和分发流程需遵守该许可证。
