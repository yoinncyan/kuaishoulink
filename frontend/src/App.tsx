import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type Counts = Record<string, number>;

type RecentEvent = {
  seq?: number;
  type?: string;
  at?: string;
  method?: string;
  url?: string;
  status?: number;
  resource_type?: string;
  mime_type?: string;
  operation_names?: string[];
  file?: string;
  action?: string;
  error?: string;
};

type Probe = {
  session_id: string;
  keyword: string;
  active: boolean;
  session_dir: string;
  event_counts: Counts;
  resource_counts: Counts;
  body_count: number;
  body_bytes: number;
  operation_counts: Counts;
  top_endpoints: Counts;
  errors: string[];
  risk_controls: Array<{
    request_id: string;
    url: string;
    intercept_result: string;
    status: number;
    at: string;
  }>;
  extracted: {
    keyword: string;
    search_cursor?: string;
    successful_search_responses: number;
    failed_search_responses: number;
    comment_responses: number;
    video_count: number;
    comment_count_resolved: number;
    qualifying_over_50: number;
    videos: Array<{
      video_id: string;
      video_url: string;
      title: string;
      author_name: string;
      author_id?: string;
      comment_count?: number;
      matched_keywords: string[];
    }>;
  };
  recent_events: RecentEvent[];
};

type Status = {
  state: "stopped" | "starting" | "running" | "stopping" | "error";
  probe_state: "stopped" | "starting" | "running" | "stopping" | "error";
  browser_state: "stopped" | "starting" | "running" | "stopping" | "error";
  started_at?: string;
  page_url?: string;
  last_error?: string;
  headless: boolean;
  profile_dir: string;
  probe: Probe | null;
};

type SessionSummary = {
  session_id: string;
  keyword?: string;
  complete?: boolean;
  body_count?: number;
  started_at?: string;
};

const initialStatus: Status = {
  state: "stopped",
  probe_state: "stopped",
  browser_state: "stopped",
  headless: false,
  profile_dir: "",
  probe: null,
};

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail ?? `HTTP ${response.status}`);
  }
  return response.json();
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function shortUrl(value?: string): string {
  if (!value) return "—";
  try {
    const url = new URL(value);
    return `${url.host}${url.pathname}`;
  } catch {
    return value;
  }
}

export default function App() {
  const [keyword, setKeyword] = useState("vpn");
  const [status, setStatus] = useState<Status>(initialStatus);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [screenshotVersion, setScreenshotVersion] = useState(Date.now());

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextSessions] = await Promise.all([
        api<Status>("/api/probe/status"),
        api<SessionSummary[]>("/api/probe/sessions"),
      ]);
      setStatus(nextStatus);
      setSessions(nextSessions);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function start(event: FormEvent) {
    event.preventDefault();
    if (!keyword.trim()) return;
    setBusy(true);
    setMessage("正在启动 CloakBrowser 并连接 CDP…");
    try {
      const next = await api<Status>("/api/probe/start", {
        method: "POST",
        body: JSON.stringify({ keyword }),
      });
      setStatus(next);
      setMessage("探针已启动。可在快手页面登录、滚动或操作搜索结果。");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    setMessage("正在整理响应文件并生成 summary.json…");
    try {
      await api("/api/probe/stop", { method: "POST", body: "{}" });
      setMessage("探针已停止，捕获会话已保存。");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function browserAction(
    endpoint: "open-login" | "reload" | "close",
    successMessage: string,
  ) {
    setBusy(true);
    try {
      const next = await api<Status>(`/api/browser/${endpoint}`, {
        method: "POST",
        body: "{}",
      });
      setStatus(next);
      setScreenshotVersion(Date.now());
      setMessage(successMessage);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function mark(action: string) {
    try {
      await api("/api/probe/mark", {
        method: "POST",
        body: JSON.stringify({ action, details: { keyword } }),
      });
      setMessage(`已写入动作标记：${action}`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function inspectVideo(videoId: string) {
    setBusy(true);
    setMessage(`正在读取 ${videoId} 的首个 commentListQuery…`);
    try {
      const result = await api<{ comment_count: number; qualifies_default: boolean }>(
        "/api/probe/inspect-video",
        {
          method: "POST",
          body: JSON.stringify({ video_id: videoId, timeout_seconds: 15 }),
        },
      );
      setMessage(`评论总数：${result.comment_count}${result.qualifies_default ? "，符合 > 50" : ""}`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  const running = status.probe_state === "running" || status.probe_state === "starting";
  const browserRunning = status.browser_state === "running";
  const counts = status.probe?.event_counts ?? {};
  const recent = useMemo(
    () => [...(status.probe?.recent_events ?? [])].reverse().slice(0, 40),
    [status.probe?.recent_events],
  );

  return (
    <main>
      <header className="topbar">
        <div>
          <div className="eyebrow">CLOAKBROWSER · CDP NETWORK</div>
          <h1>快手网络探针</h1>
        </div>
        <div className="status-cluster">
          <div className={`status status-${status.browser_state}`}>
            <span />BROWSER {status.browser_state.toUpperCase()}
          </div>
          <div className={`status status-${status.probe_state}`}>
            <span />PROBE {status.probe_state.toUpperCase()}
          </div>
        </div>
      </header>

      <section className="hero panel">
        <div>
          <p className="section-label">P0 · 接口发现</p>
          <h2>捕获搜索接口，不读取 DOM 业务数据</h2>
          <p className="muted">
            入口固定为 <code>kuaishou.com/search/关键词</code>。探针在导航前连接 CDP，
            记录请求、响应 Body、GraphQL operationName 与 WebSocket 帧。
          </p>
        </div>
        <form onSubmit={start} className="start-form">
          <label htmlFor="keyword">探测关键词</label>
          <div className="input-row">
            <input
              id="keyword"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              disabled={running || busy}
              placeholder="例如：vpn"
            />
            <button className="primary" disabled={running || busy || !keyword.trim()}>
              启动探针
            </button>
            <button type="button" className="danger" disabled={!running || busy} onClick={stop}>
              停止并保存
            </button>
          </div>
        </form>
        <div className="browser-actions">
          <button
            type="button"
            disabled={busy}
            onClick={() => void browserAction("open-login", "快手登录页已打开；完成登录后再启动探针。")}
          >
            打开快手登录页
          </button>
          <button
            type="button"
            disabled={!browserRunning || busy}
            onClick={() => void browserAction("reload", "当前快手页面已重新加载。")}
          >
            重新加载页面
          </button>
          <button
            type="button"
            disabled={!browserRunning || busy}
            onClick={() => void browserAction("close", "浏览器已关闭，登录 Profile 已保存。")}
          >
            关闭浏览器
          </button>
          <span>先登录并建立持久化 Profile，再执行接口探测。</span>
        </div>
        {message && <div className="message">{message}</div>}
      </section>

      <section className="stats-grid">
        <Metric label="搜索视频" value={status.probe?.extracted?.video_count ?? 0} />
        <Metric label="评论数已取得" value={status.probe?.extracted?.comment_count_resolved ?? 0} />
        <Metric label="评论数 > 50" value={status.probe?.extracted?.qualifying_over_50 ?? 0} />
        <Metric label="网络响应" value={counts.response ?? 0} />
        <Metric label="已保存 Body" value={`${status.probe?.body_count ?? 0} · ${formatBytes(status.probe?.body_bytes ?? 0)}`} />
      </section>

      {!!status.probe?.extracted?.videos?.length && (
        <section className="panel extracted-panel">
          <div className="panel-title-row">
            <div>
              <p className="section-label">网络响应解析结果</p>
              <h3>视频元数据与评论总数</h3>
            </div>
            <span className="muted-small">
              搜索游标：{status.probe.extracted.search_cursor ?? "—"}
            </span>
          </div>
          <div className="table-wrap result-table">
            <table>
              <thead>
                <tr>
                  <th>视频 ID</th>
                  <th>视频标题</th>
                  <th>作者</th>
                  <th>评论总数</th>
                  <th>关键词</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {status.probe.extracted.videos.map((video) => (
                  <tr key={video.video_id}>
                    <td><a href={video.video_url} target="_blank" rel="noreferrer">{video.video_id}</a></td>
                    <td title={video.title}>{video.title || "—"}</td>
                    <td>{video.author_name || "—"}</td>
                    <td className={typeof video.comment_count === "number" && video.comment_count > 50 ? "qualified" : ""}>
                      {video.comment_count ?? "等待详情响应"}
                    </td>
                    <td>{video.matched_keywords.join(", ")}</td>
                    <td>
                      <button
                        className="table-action"
                        disabled={!running || busy || typeof video.comment_count === "number"}
                        onClick={() => void inspectVideo(video.video_id)}
                      >
                        {typeof video.comment_count === "number" ? "已取得" : "检测评论数"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {!!status.probe?.risk_controls?.length && (
        <section className="risk-banner">
          <strong>检测到快手风控响应</strong>
          <span>
            {status.probe.risk_controls.at(-1)?.intercept_result} · {shortUrl(status.probe.risk_controls.at(-1)?.url)}
          </span>
          <small>网络连接本身正常，但业务接口被风险控制拦截。</small>
        </section>
      )}

      <section className="workspace-grid">
        <div className="panel main-panel">
          <div className="panel-title-row">
            <div>
              <p className="section-label">实时网络事件</p>
              <h3>{status.probe?.session_id ?? "尚未启动会话"}</h3>
            </div>
            <div className="actions">
              <button disabled={!running} onClick={() => void mark("login_completed")}>标记登录完成</button>
              <button disabled={!running} onClick={() => void mark("scroll_started")}>标记开始滚动</button>
            </div>
          </div>
          <div className="current-url" title={status.page_url}>
            <span>页面</span>{status.page_url ?? "—"}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>类型</th>
                  <th>资源</th>
                  <th>状态/方法</th>
                  <th>接口</th>
                  <th>Operation</th>
                </tr>
              </thead>
              <tbody>
                {recent.length === 0 && (
                  <tr><td colSpan={6} className="empty">等待网络事件…</td></tr>
                )}
                {recent.map((event, index) => (
                  <tr key={`${event.seq ?? index}-${event.type}`}>
                    <td>{event.seq ?? "—"}</td>
                    <td><span className="event-type">{event.type ?? "—"}</span></td>
                    <td>{event.resource_type ?? "—"}</td>
                    <td>{event.status ?? event.method ?? "—"}</td>
                    <td title={event.url}>{event.file ?? shortUrl(event.url) ?? event.action}</td>
                    <td>{event.operation_names?.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="panel side-panel">
          <p className="section-label">浏览器预览</p>
          {browserRunning && (
            <button className="preview-refresh" onClick={() => setScreenshotVersion(Date.now())}>
              刷新预览
            </button>
          )}
          <div className="browser-preview">
            {browserRunning ? (
              <img
                src={`/api/browser/screenshot?v=${screenshotVersion}`}
                alt="当前快手浏览器页面"
                onClick={() => setScreenshotVersion(Date.now())}
              />
            ) : (
              <p className="empty">浏览器尚未启动</p>
            )}
          </div>
          <p className="section-label">已发现接口</p>
          <div className="endpoint-list">
            {Object.entries(status.probe?.top_endpoints ?? {}).slice(0, 12).map(([endpoint, count]) => (
              <div className="endpoint" key={endpoint}>
                <span title={endpoint}>{endpoint}</span><strong>{count}</strong>
              </div>
            ))}
            {!Object.keys(status.probe?.top_endpoints ?? {}).length && <p className="empty">暂无数据</p>}
          </div>
          <p className="section-label section-gap">历史会话</p>
          <div className="session-list">
            {sessions.slice(0, 8).map((session) => (
              <div className="session" key={session.session_id}>
                <div><strong>{session.keyword ?? "未知关键词"}</strong><small>{session.session_id}</small></div>
                <span className={session.complete ? "complete" : "incomplete"}>
                  {session.complete ? "完整" : "进行中"}
                </span>
              </div>
            ))}
            {!sessions.length && <p className="empty">尚无捕获记录</p>}
          </div>
        </aside>
      </section>

      {(status.last_error || status.probe?.errors?.length) && (
        <section className="panel error-panel">
          <p className="section-label">错误</p>
          {[status.last_error, ...(status.probe?.errors ?? [])].filter(Boolean).map((error, index) => (
            <pre key={index}>{error}</pre>
          ))}
        </section>
      )}
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="metric panel">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
