import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type Counts = Record<string, number>;

const FULL_KEYWORD_COUNT = 2463;

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
  profile_id?: string;
  profile_name?: string;
  profile_count?: number;
  profile_proxy_configured?: boolean;
  profile_dir: string;
  probe: Probe | null;
};

type BrowserProfile = {
  profile_id: string;
  name: string;
  active: boolean;
  proxy?: string | null;
  created_at?: string;
  updated_at?: string;
  last_used_at?: string | null;
  profile_dir: string;
  identity_id: string;
  fingerprint_seed: string;
  identity_created_at?: string;
};

type ProfileList = {
  active_profile_id: string;
  profile_count: number;
  profiles: BrowserProfile[];
};

type SessionSummary = {
  session_id: string;
  keyword?: string;
  complete?: boolean;
  body_count?: number;
  started_at?: string;
};

type SamplingMetric = {
  keyword: string;
  collection_mode?: "anonymous_repeat" | "logged_in_full_scroll";
  completed_rounds?: number;
  attempted_rounds?: number;
  successful_rounds: number;
  failed_rounds: number;
  unique_videos: number;
  page_responses?: number;
  scroll_count?: number;
  search_cursor?: string;
  end_marker_visible?: boolean;
};

type SamplingProgress = {
  collection_mode?: "anonymous_repeat" | "logged_in_full_scroll";
  phase?: string;
  event?: string;
  current_keyword?: string;
  current_keyword_position?: number;
  keyword_count?: number;
  current_completed_rounds?: number;
  target_search_rounds?: number;
  global_unique_links?: number;
  total_identity_resets?: number;
  updated_at?: string;
  details?: Record<string, unknown>;
};

type SamplingStatus = {
  state: "stopped" | "starting" | "running" | "pausing" | "paused" | "completed" | "error";
  running: boolean;
  pid?: number;
  active_run_dir?: string;
  active_run_profile_id?: string;
  started_at?: string;
  ended_at?: string;
  last_error?: string;
  returncode?: number;
  config: Record<string, number | boolean | string>;
  progress: SamplingProgress | null;
  metrics: SamplingMetric[];
  run_unique_links: number;
  master_unique_links: number;
  keyword_library_count?: number;
  comment_progress?: {
    phase?: string;
    event?: string;
    stats?: {
      source: number;
      completed: number;
      resolved: number;
      qualified: number;
      not_qualified: number;
      unavailable: number;
      failed_or_pending: number;
      attempted_unresolved: number;
    };
    details?: Record<string, unknown>;
    progress_percent?: number;
    query_attempts?: number;
    recent_errors?: Array<{
      video_id: string;
      status?: string;
      attempts: number;
      error?: string;
      intercept_result?: string;
      queried_at?: string;
    }>;
    checkpoint_path?: string;
    output_path?: string;
    updated_at?: string;
  } | null;
  log_tail: string[];
};

const initialSamplingStatus: SamplingStatus = {
  state: "stopped",
  running: false,
  config: {},
  progress: null,
  metrics: [],
  run_unique_links: 0,
  master_unique_links: 0,
  comment_progress: null,
  log_tail: [],
};

const initialStatus: Status = {
  state: "stopped",
  probe_state: "stopped",
  browser_state: "stopped",
  headless: false,
  profile_dir: "",
  probe: null,
};

const initialProfiles: ProfileList = {
  active_profile_id: "",
  profile_count: 0,
  profiles: [],
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
  const [profiles, setProfiles] = useState<ProfileList>(initialProfiles);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [newProfileName, setNewProfileName] = useState("");
  const [newProfileProxy, setNewProfileProxy] = useState("");
  const [profileBusy, setProfileBusy] = useState(false);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [taskStatus, setTaskStatus] = useState<SamplingStatus>(initialSamplingStatus);
  const [busy, setBusy] = useState(false);
  const [taskBusy, setTaskBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [screenshotVersion, setScreenshotVersion] = useState(Date.now());
  const [taskConfig, setTaskConfig] = useState({
    collection_mode: "logged_in_full_scroll" as
      | "anonymous_repeat"
      | "logged_in_full_scroll",
    loops: 20,
    min_interval: 6,
    max_interval: 12,
    max_consecutive: 5,
    open_browser_window: true,
    auto_reset_identity: false,
    scroll_min_interval: 1.5,
    scroll_max_interval: 3,
    max_scrolls_per_keyword: 300,
  });

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextSessions, nextTaskStatus, nextProfiles] = await Promise.all([
        api<Status>("/api/probe/status"),
        api<SessionSummary[]>("/api/probe/sessions"),
        api<SamplingStatus>("/api/task/status"),
        api<ProfileList>("/api/profiles"),
      ]);
      setStatus(nextStatus);
      setSessions(nextSessions);
      setTaskStatus(nextTaskStatus);
      setProfiles(nextProfiles);
      setSelectedProfileId((current) =>
        current && nextProfiles.profiles.some((profile) => profile.profile_id === current)
          ? current
          : nextProfiles.active_profile_id,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function createProfile() {
    const name = newProfileName.trim();
    if (!name) {
      setMessage("请输入新 Profile 名称。");
      return;
    }
    setProfileBusy(true);
    setMessage(`正在创建并切换到 Profile“${name}”…`);
    try {
      const result = await api<ProfileList & { browser: Status; created: BrowserProfile }>(
        "/api/profiles",
        {
          method: "POST",
          body: JSON.stringify({
            name,
            proxy: newProfileProxy.trim() || null,
            activate: true,
            open_browser_window: taskConfig.open_browser_window,
          }),
        },
      );
      setProfiles(result);
      setStatus(result.browser);
      setSelectedProfileId(result.created.profile_id);
      setNewProfileName("");
      setNewProfileProxy("");
      setScreenshotVersion(Date.now());
      setMessage(`Profile“${name}”已创建并隔离启动，请在新窗口登录对应账号。`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setProfileBusy(false);
    }
  }

  async function activateProfile() {
    if (!selectedProfileId || selectedProfileId === profiles.active_profile_id) return;
    const selected = profiles.profiles.find(
      (profile) => profile.profile_id === selectedProfileId,
    );
    setProfileBusy(true);
    setMessage(`正在切换到 Profile“${selected?.name ?? selectedProfileId}”…`);
    try {
      const next = await api<Status>(`/api/profiles/${selectedProfileId}/activate`, {
        method: "POST",
        body: JSON.stringify({ open_browser_window: taskConfig.open_browser_window }),
      });
      setStatus(next);
      setScreenshotVersion(Date.now());
      setMessage(`已切换到“${selected?.name ?? selectedProfileId}”，其账号、指纹和缓存独立加载。`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setProfileBusy(false);
    }
  }

  async function editSelectedProfile() {
    const selected = profiles.profiles.find(
      (profile) => profile.profile_id === selectedProfileId,
    );
    if (!selected) return;
    const name = window.prompt("Profile 名称", selected.name);
    if (name === null) return;
    const proxy = window.prompt(
      "独立代理 URL（留空表示不使用代理）",
      selected.proxy ?? "",
    );
    if (proxy === null) return;
    setProfileBusy(true);
    try {
      const result = await api<ProfileList & { updated: BrowserProfile }>(
        `/api/profiles/${selected.profile_id}`,
        {
          method: "PATCH",
          body: JSON.stringify({ name, proxy: proxy.trim() || null }),
        },
      );
      setProfiles(result);
      setMessage(`Profile“${result.updated.name}”已更新。代理变更将在该 Profile 下次启动时生效。`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setProfileBusy(false);
    }
  }

  async function deleteSelectedProfile() {
    const selected = profiles.profiles.find(
      (profile) => profile.profile_id === selectedProfileId,
    );
    if (!selected || selected.active) return;
    const confirmed = window.confirm(
      `删除 Profile“${selected.name}”会永久删除它的 Cookie、登录、指纹和缓存；累计链接不受影响。确定删除吗？`,
    );
    if (!confirmed) return;
    setProfileBusy(true);
    try {
      const result = await api<ProfileList & { deleted: BrowserProfile }>(
        `/api/profiles/${selected.profile_id}`,
        { method: "DELETE" },
      );
      setProfiles(result);
      setSelectedProfileId(result.active_profile_id);
      setMessage(`Profile“${selected.name}”已删除，累计链接和其他 Profile 未改动。`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setProfileBusy(false);
    }
  }

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

  async function startTask() {
    setTaskBusy(true);
    setMessage("正在从检查点启动批量采集…");
    try {
      const next = await api<SamplingStatus>("/api/task/start", {
        method: "POST",
        body: JSON.stringify({
          stage: "search",
          collection_mode: taskConfig.collection_mode,
          limit: keywordLibraryCount,
          loops: taskConfig.loops,
          min_interval: taskConfig.min_interval,
          max_interval: taskConfig.max_interval,
          max_consecutive: taskConfig.max_consecutive,
          max_attempts: 250,
          open_browser_window: taskConfig.open_browser_window,
          auto_reset_identity: taskConfig.auto_reset_identity,
          scroll_min_interval: taskConfig.scroll_min_interval,
          scroll_max_interval: taskConfig.scroll_max_interval,
          max_scrolls_per_keyword: taskConfig.max_scrolls_per_keyword,
        }),
      });
      setTaskStatus(next);
      setMessage(
        taskConfig.collection_mode === "logged_in_full_scroll"
          ? "登录态全量滚动已开始：每个关键词只搜索一次，持续下滑到“没有更多了”。"
          : "匿名多轮采集已开始，后续进度会自动保存到累计主记录。",
      );
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setTaskBusy(false);
    }
  }

  async function startCommentStage() {
    setTaskBusy(true);
    setMessage("正在启动第二阶段评论数量查询…");
    try {
      const next = await api<SamplingStatus>("/api/task/start", {
        method: "POST",
        body: JSON.stringify({
          stage: "comments",
          open_browser_window: taskConfig.open_browser_window,
          auto_reset_identity: false,
          comment_min_interval: 1.5,
          comment_max_interval: 3.5,
        }),
      });
      setTaskStatus(next);
      setMessage("第二阶段已开始：正在逐条获取评论总数并筛选 > 50。");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setTaskBusy(false);
    }
  }

  async function pauseTask() {
    setTaskBusy(true);
    setMessage("正在暂停并写入检查点…");
    try {
      const next = await api<SamplingStatus>("/api/task/pause", {
        method: "POST",
        body: "{}",
      });
      setTaskStatus(next);
      setMessage("批量采集已暂停，进度和累计链接均已保存。");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setTaskBusy(false);
    }
  }

  async function resetTaskIdentity() {
    const activeProfile = profiles.profiles.find((profile) => profile.active);
    const confirmed = window.confirm(
      `深度重置只会清空当前 Profile“${activeProfile?.name ?? "未知"}”的 Cookie、缓存和指纹身份；其他 Profile、累计链接和任务进度会保留。确定继续吗？`,
    );
    if (!confirmed) return;
    setTaskBusy(true);
    setMessage("正在深度重置浏览器身份…");
    try {
      await api("/api/task/reset-identity", {
        method: "POST",
        body: JSON.stringify({ open_browser_window: taskConfig.open_browser_window }),
      });
      setMessage("浏览器身份已深度重置，采集记录和检查点未删除。");
      setScreenshotVersion(Date.now());
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setTaskBusy(false);
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
  const taskRunning = taskStatus.state === "running" || taskStatus.state === "starting";
  const taskPausing = taskStatus.state === "pausing";
  const commentRunning = taskRunning && taskStatus.config.stage === "comments";
  const activeCollectionMode =
    taskStatus.progress?.collection_mode ??
    taskStatus.config.collection_mode ??
    "anonymous_repeat";
  const selectedCollectionMode =
    taskRunning && taskStatus.config.stage === "search"
      ? activeCollectionMode
      : taskConfig.collection_mode;
  const loggedInFullScroll = selectedCollectionMode === "logged_in_full_scroll";
  const selectedRunIsActive =
    activeCollectionMode === selectedCollectionMode &&
    (taskStatus.active_run_profile_id ?? "kuaishou") ===
      (profiles.active_profile_id || "kuaishou");
  const commentsUpToDate =
    taskStatus.comment_progress?.phase === "completed" &&
    (taskStatus.comment_progress?.stats?.source ?? 0) >= taskStatus.master_unique_links;
  const taskCompletedRounds = selectedRunIsActive
    ? taskStatus.metrics.reduce(
        (total, metric) => total + (metric.completed_rounds ?? metric.attempted_rounds ?? 0),
        0,
      )
    : 0;
  const keywordLibraryCount = taskStatus.keyword_library_count || FULL_KEYWORD_COUNT;
  const taskTargetRounds = keywordLibraryCount * (loggedInFullScroll ? 1 : taskConfig.loops);
  const counts = status.probe?.event_counts ?? {};
  const recent = useMemo(
    () => [...(status.probe?.recent_events ?? [])].reverse().slice(0, 40),
    [status.probe?.recent_events],
  );
  const activeProfile = profiles.profiles.find((profile) => profile.active);
  const selectedProfile = profiles.profiles.find(
    (profile) => profile.profile_id === selectedProfileId,
  );
  const profileSwitchBlocked = taskRunning || taskPausing || running || profileBusy;

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

      <section className="profile-panel panel">
        <div className="panel-title-row">
          <div>
            <p className="section-label">账号与浏览器 Profile</p>
            <h2>多账号隔离与切换</h2>
            <p className="muted">
              每个 Profile 独立保存账号、Cookie、LocalStorage、浏览器缓存和指纹身份；采集进度按 Profile 隔离，链接继续汇总到同一累计去重总表。
            </p>
          </div>
          <div className="profile-active-badge">
            <span>当前 Profile</span>
            <strong>{activeProfile?.name ?? "尚未加载"}</strong>
          </div>
        </div>

        <div className="profile-selector-row">
          <label>
            选择 Profile
            <select
              value={selectedProfileId}
              disabled={profileSwitchBlocked}
              onChange={(event) => setSelectedProfileId(event.target.value)}
            >
              {profiles.profiles.map((profile) => (
                <option key={profile.profile_id} value={profile.profile_id}>
                  {profile.active ? "当前 · " : ""}{profile.name}
                  {profile.proxy ? " · 独立代理" : ""}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="primary"
            disabled={
              profileSwitchBlocked ||
              !selectedProfile ||
              selectedProfile.profile_id === profiles.active_profile_id
            }
            onClick={() => void activateProfile()}
          >
            切换并打开
          </button>
          <button
            type="button"
            className="secondary"
            disabled={profileSwitchBlocked || !selectedProfile}
            onClick={() => void editSelectedProfile()}
          >
            编辑名称/代理
          </button>
          <button
            type="button"
            className="danger"
            disabled={
              profileSwitchBlocked ||
              !selectedProfile ||
              selectedProfile.active ||
              profiles.profile_count <= 1
            }
            onClick={() => void deleteSelectedProfile()}
          >
            删除所选
          </button>
        </div>

        <div className="profile-summary-grid">
          <TaskMetric label="Profile 数量" value={profiles.profile_count} />
          <TaskMetric label="当前账号" value={activeProfile?.name ?? "—"} />
          <TaskMetric
            label="独立身份 ID"
            value={activeProfile?.identity_id.slice(0, 12) ?? "—"}
          />
          <TaskMetric
            label="独立代理"
            value={activeProfile?.proxy ? "已配置" : "未配置"}
          />
        </div>

        <div className="profile-create-row">
          <label>
            新 Profile 名称
            <input
              value={newProfileName}
              maxLength={60}
              placeholder="例如：账号二"
              disabled={profileSwitchBlocked}
              onChange={(event) => setNewProfileName(event.target.value)}
            />
          </label>
          <label>
            独立代理（可选）
            <input
              value={newProfileProxy}
              placeholder="http://user:pass@HOST:PORT"
              disabled={profileSwitchBlocked}
              onChange={(event) => setNewProfileProxy(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="primary"
            disabled={profileSwitchBlocked || !newProfileName.trim()}
            onClick={() => void createProfile()}
          >
            新建并切换
          </button>
        </div>
        <div className="task-footnote">
          <span>Profile ID：{activeProfile?.profile_id ?? "—"}</span>
          <span>切换前会关闭当前浏览器进程；其他 Profile 的目录和缓存不会被读取或清理。</span>
        </div>
      </section>

      <section className="task-panel panel">
        <div className="panel-title-row">
          <div>
            <p className="section-label">批量采集控制</p>
            <h2>开始、暂停和恢复都在这里完成</h2>
            <p className="muted">
              自动从快手首页搜索框触发搜索，按视频链接全局去重，并持续更新累计主记录。
            </p>
          </div>
          <div className={`status status-${taskStatus.state}`}>
            <span />TASK {taskStatus.state.toUpperCase()}
          </div>
        </div>

        <div className="task-controls">
          <button
            type="button"
            className="primary task-main-button"
            disabled={taskRunning || taskPausing || taskBusy}
            onClick={() => void startTask()}
          >
            {loggedInFullScroll
              ? selectedRunIsActive && taskStatus.metrics.length
                ? "继续登录态全量滚动"
                : "开始登录态全量滚动"
              : selectedRunIsActive && taskStatus.metrics.length
                ? "继续匿名多轮采集"
                : "开始匿名多轮采集"}
          </button>
          <button
            type="button"
            className="warning task-main-button"
            disabled={!taskRunning || taskBusy}
            onClick={() => void pauseTask()}
          >
            暂停并保存
          </button>
          <button
            type="button"
            className="danger task-main-button"
            disabled={loggedInFullScroll || taskRunning || taskPausing || taskBusy}
            onClick={() => void resetTaskIdentity()}
          >
            深度重置身份
          </button>
        </div>

        <label className="window-toggle">
          <input
            type="checkbox"
            checked={loggedInFullScroll}
            disabled={taskRunning || taskPausing || taskBusy}
            onChange={(event) =>
              setTaskConfig((value) => ({
                ...value,
                collection_mode: event.target.checked
                  ? "logged_in_full_scroll"
                  : "anonymous_repeat",
                auto_reset_identity: event.target.checked
                  ? false
                  : value.auto_reset_identity,
              }))
            }
          />
          <span className="toggle-track"><span /></span>
          <span>
            <strong>登录后全量滚动</strong>
            <small>
              默认开启；每条关键词只搜索一次，持续下滑到“没有更多了”。该模式使用独立进度，但链接合并进同一累计去重总表。
            </small>
          </span>
        </label>

        <label className="window-toggle">
          <input
            type="checkbox"
            checked={taskConfig.open_browser_window}
            disabled={taskRunning || taskPausing || taskBusy}
            onChange={(event) =>
              setTaskConfig((value) => ({
                ...value,
                open_browser_window: event.target.checked,
              }))
            }
          />
          <span className="toggle-track"><span /></span>
          <span>
            <strong>打开 CloakBrowser 窗口</strong>
            <small>默认开启；关闭后在后台运行，不抢占桌面焦点。</small>
          </span>
        </label>

        <label className="window-toggle">
          <input
            type="checkbox"
            checked={taskConfig.auto_reset_identity}
            disabled={loggedInFullScroll || taskRunning || taskPausing || taskBusy}
            onChange={(event) =>
              setTaskConfig((value) => ({
                ...value,
                auto_reset_identity: event.target.checked,
              }))
            }
          />
          <span className="toggle-track"><span /></span>
          <span>
            <strong>自动深度重置</strong>
            <small>
              {loggedInFullScroll
                ? "登录模式固定关闭，异常时保留账号和 Profile 并暂停。"
                : "默认关闭；匿名模式遇到真实搜索异常会暂停，等待手动重置。"}
            </small>
          </span>
        </label>

        <div className="task-config-grid">
          <label>
            关键词范围
            <input value={`全部 ${keywordLibraryCount} 条`} readOnly />
          </label>
          {loggedInFullScroll ? (
            <>
              <label>
                每词搜索次数
                <input value="固定 1 次" readOnly />
              </label>
              <label>
                最小下滑间隔（秒）
                <input
                  type="number"
                  min={0}
                  max={120}
                  step={0.1}
                  value={taskConfig.scroll_min_interval}
                  disabled={taskRunning || taskPausing}
                  onChange={(event) => setTaskConfig((value) => ({ ...value, scroll_min_interval: Number(event.target.value) }))}
                />
              </label>
              <label>
                最大下滑间隔（秒）
                <input
                  type="number"
                  min={0}
                  max={120}
                  step={0.1}
                  value={taskConfig.scroll_max_interval}
                  disabled={taskRunning || taskPausing}
                  onChange={(event) => setTaskConfig((value) => ({ ...value, scroll_max_interval: Number(event.target.value) }))}
                />
              </label>
              <label>
                单词安全下滑上限
                <input
                  type="number"
                  min={1}
                  max={2000}
                  value={taskConfig.max_scrolls_per_keyword}
                  disabled={taskRunning || taskPausing}
                  onChange={(event) => setTaskConfig((value) => ({ ...value, max_scrolls_per_keyword: Number(event.target.value) }))}
                />
              </label>
            </>
          ) : (
            <>
              <label>
                每词总次数
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={taskConfig.loops}
                  disabled={taskRunning || taskPausing}
                  onChange={(event) => setTaskConfig((value) => ({ ...value, loops: Number(event.target.value) }))}
                />
              </label>
              <label>
                最小间隔（秒）
                <input
                  type="number"
                  min={0}
                  max={120}
                  value={taskConfig.min_interval}
                  disabled={taskRunning || taskPausing}
                  onChange={(event) => setTaskConfig((value) => ({ ...value, min_interval: Number(event.target.value) }))}
                />
              </label>
              <label>
                最大间隔（秒）
                <input
                  type="number"
                  min={0}
                  max={120}
                  value={taskConfig.max_interval}
                  disabled={taskRunning || taskPausing}
                  onChange={(event) => setTaskConfig((value) => ({ ...value, max_interval: Number(event.target.value) }))}
                />
              </label>
              <label>
                同词连续上限
                <input
                  type="number"
                  min={1}
                  max={5}
                  value={taskConfig.max_consecutive}
                  disabled={taskRunning || taskPausing}
                  onChange={(event) => setTaskConfig((value) => ({ ...value, max_consecutive: Number(event.target.value) }))}
                />
              </label>
            </>
          )}
        </div>

        <div className="task-progress-grid">
          <TaskMetric label="总进度" value={`${taskCompletedRounds}/${taskTargetRounds}`} />
          <TaskMetric
            label="当前关键词"
            value={selectedRunIsActive ? taskStatus.progress?.current_keyword ?? "—" : "—"}
          />
          <TaskMetric
            label="当前词进度"
            value={selectedRunIsActive
              ? `${taskStatus.progress?.current_completed_rounds ?? 0}/${taskStatus.progress?.target_search_rounds ?? (loggedInFullScroll ? 1 : taskConfig.loops)}`
              : `0/${loggedInFullScroll ? 1 : taskConfig.loops}`}
          />
          {loggedInFullScroll && (
            <TaskMetric
              label="当前词分页响应"
              value={selectedRunIsActive
                ? String(taskStatus.progress?.details?.page_responses ?? 0)
                : "0"}
            />
          )}
          {loggedInFullScroll && (
            <TaskMetric
              label="当前词去重链接"
              value={selectedRunIsActive
                ? String(taskStatus.progress?.details?.unique_videos ?? 0)
                : "0"}
            />
          )}
          <TaskMetric label="本模式去重" value={selectedRunIsActive ? taskStatus.run_unique_links : 0} />
          <TaskMetric label="累计去重" value={taskStatus.master_unique_links} />
        </div>

        <div className="task-footnote">
          <span>
            运行目录：{selectedRunIsActive
              ? taskStatus.active_run_dir ?? "将在首次启动时创建"
              : "启动时创建或切换到该模式的独立检查点"}
          </span>
          <span>
            下次启动：{loggedInFullScroll ? "登录态全量滚动" : "匿名多轮"} · {taskConfig.open_browser_window ? "打开 CloakBrowser 窗口" : "后台无窗口模式"}
          </span>
        </div>
        {taskStatus.progress?.event === "manual_reset_required" && (
          <div className="task-error">
            关键词“{taskStatus.progress.current_keyword ?? "未知"}”发生真实搜索异常：
            {String(taskStatus.progress.details?.error ?? "接口返回失败")}
            。任务已暂停，请点击“深度重置身份”后继续。
          </div>
        )}
        {taskStatus.progress?.event === "logged_in_attention_required" && (
          <div className="task-error">
            <strong>登录态网络分页已暂停</strong>
            <div className="task-error-details">
              <span>关键词：{taskStatus.progress.current_keyword ?? "未知"}</span>
              <span>
                阶段：{taskStatus.progress.details?.failure_stage === "pagination"
                  ? "滚动请求下一页"
                  : "首次搜索"}
              </span>
              <span>接口页数：{String(taskStatus.progress.details?.page_responses ?? 0)}</span>
              <span>已保存链接：{String(taskStatus.progress.details?.saved_unique_videos ?? 0)}</span>
              <span>请求游标：{String(taskStatus.progress.details?.requested_cursor ?? "—")}</span>
              <span>HTTP：{String(taskStatus.progress.details?.http_status ?? "—")}</span>
              <span>响应 result：{String(taskStatus.progress.details?.response_result ?? "—")}</span>
              <span>风控头：{String(taskStatus.progress.details?.error ?? "—")}</span>
            </div>
            <div className="task-error-explanation">
              这类异常通常不会在页面显示报错；页面可能仍显示首屏内容，甚至显示“没有更多了”。当前网络响应没有返回下一页视频，因此没有把现有结果误判为完整结果。账号、Cookie、指纹和已采集链接均已保留，稍后点击继续会重新完整采集当前关键词；不要执行深度重置。
            </div>
          </div>
        )}
        {taskStatus.last_error && <div className="task-error">{taskStatus.last_error}</div>}
      </section>

      <section className="comment-task-panel panel">
        <div className="panel-title-row">
          <div>
            <p className="section-label">第二阶段 · 评论数量</p>
            <h2>逐条查询评论总数，筛选严格大于 50</h2>
            <p className="muted">
              只调用 GraphQL commentListQuery 获取总数，不打开视频详情，也不读取评论内容。
            </p>
          </div>
          <div className={`status status-${taskStatus.comment_progress?.phase ?? "stopped"}`}>
            <span />COMMENTS {(taskStatus.comment_progress?.phase ?? "NOT STARTED").toUpperCase()}
          </div>
        </div>

        <div className="task-controls">
          <button
            type="button"
            className="secondary task-main-button"
            disabled={
              taskRunning ||
              taskPausing ||
              taskBusy ||
              commentsUpToDate ||
              (!taskStatus.comment_progress && taskStatus.progress?.phase !== "completed")
            }
            onClick={() => void startCommentStage()}
          >
            {commentsUpToDate
              ? "第二阶段已完成"
              : taskStatus.comment_progress?.stats?.resolved
                ? "继续第二阶段"
                : "开始第二阶段"}
          </button>
          <button
            type="button"
            className="warning task-main-button"
            disabled={!commentRunning || taskBusy}
            onClick={() => void pauseTask()}
          >
            暂停第二阶段并保存
          </button>
          {!!taskStatus.comment_progress?.stats?.resolved && (
            <a className="output-link" href="/api/task/comment-output" target="_blank" rel="noreferrer">
              查看当前 Markdown
            </a>
          )}
        </div>

        <div className="comment-progress-track" aria-label="第二阶段进度">
          <span style={{ width: `${taskStatus.comment_progress?.progress_percent ?? 0}%` }} />
        </div>
        <div className="comment-progress-summary">
          <TaskMetric
            label="查询进度"
            value={`${taskStatus.comment_progress?.stats?.completed ?? taskStatus.comment_progress?.stats?.resolved ?? 0}/${taskStatus.comment_progress?.stats?.source ?? taskStatus.master_unique_links}`}
          />
          <TaskMetric label="完成百分比" value={`${taskStatus.comment_progress?.progress_percent ?? 0}%`} />
          <TaskMetric label="评论数 > 50" value={taskStatus.comment_progress?.stats?.qualified ?? 0} />
          <TaskMetric label="评论数 ≤ 50" value={taskStatus.comment_progress?.stats?.not_qualified ?? 0} />
          <TaskMetric label="评论总数不可用" value={taskStatus.comment_progress?.stats?.unavailable ?? 0} />
          <TaskMetric label="待处理/错误" value={taskStatus.comment_progress?.stats?.failed_or_pending ?? 0} />
        </div>

        <div className="task-footnote">
          <span>检查点：{taskStatus.comment_progress?.checkpoint_path ?? "尚未创建"}</span>
          <span>最后更新：{taskStatus.comment_progress?.updated_at ?? "—"}</span>
        </div>

        {!!taskStatus.comment_progress?.recent_errors?.length && (
          <div className="comment-errors">
            <strong>最近查询错误</strong>
            {taskStatus.comment_progress.recent_errors.map((error) => (
              <div key={`${error.video_id}-${error.queried_at}`}>
                <code>{error.video_id}</code>
                <span>{error.status ?? "query_failed"}</span>
                <span>尝试 {error.attempts}</span>
                <span title={error.error}>{error.intercept_result ?? error.error ?? "未返回评论数量"}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="hero panel">
        <div>
          <p className="section-label">P0 · 接口发现</p>
          <h2>捕获搜索接口，不读取 DOM 业务数据</h2>
          <p className="muted">
            搜索由首页输入框和“搜索”按钮真实触发。探针在点击前连接 CDP，
            业务数据只读取请求、响应 Body、GraphQL operationName 与 WebSocket 帧。
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

function TaskMetric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="task-metric">
      <span>{label}</span>
      <strong title={String(value)}>{value}</strong>
    </div>
  );
}
