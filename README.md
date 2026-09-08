# 快手 Web 视频统计工具

基于 CloakBrowser 的快手 Web 网络响应采集与统计服务。最终形态为部署在服务器上的 Docker 服务，通过专用 Web 界面完成登录、关键词任务、实时统计和 XLSX 导出。

## 当前阶段：P0 网络探针

当前版本用于确认快手 Web 的真实数据接口，不从 DOM 文本提取视频业务数据。

已经具备：

- 单一持久化 CloakBrowser Profile；登录 Cookie 跨重启保留。
- 直接打开 `https://www.kuaishou.com/search/{keyword}`。
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
| `KUAISHOU_BROWSER_HEADLESS` | `false` | 是否使用无头浏览器 |
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
```

当前结果：2,427 条原始记录、1,537 个提纯唯一词、1,087 个建议快手采集词、1,370 个长尾词。用户明确要求所有“加速器”相关词保留，当前共 106 个，剔除表中为 0 个。

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
