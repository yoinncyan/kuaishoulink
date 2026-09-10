#!/usr/bin/env python3
"""Sample the first TSV keywords repeatedly through the local CloakBrowser API."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx


DIRECT_TITLE_TERMS = ("vpn", "翻墙", "梯子", "科学上网", "魔法上网")
ACCELERATOR_TERMS = ("加速器", "加速")
OVERSEAS_TERMS = (
    "外网",
    "海外",
    "跨境",
    "境外",
    "国际服",
    "tiktok",
    "telegram",
    "shadowrocket",
    "国区",
    "节点",
)
GAME_NETWORK_TERMS = (
    "延迟",
    "卡顿",
    "高ping",
    "steam",
    "pubg",
    "和平精英",
    "地铁逃生",
    "apex",
    "手游",
    "联机",
)


class ManualResetRequired(RuntimeError):
    """A verified search failed while automatic identity reset was disabled."""


def load_keywords(path: Path, limit: int) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if limit < 1:
        raise ValueError("keyword limit must be positive")
    return rows[:limit]


def keyword_checkpoint_path(
    runtime_dir: Path, keyword_row: dict[str, str]
) -> Path:
    """Return a filesystem-safe checkpoint path with legacy compatibility."""
    index = str(keyword_row["keyword_index"])
    keyword = str(keyword_row["keyword"])
    legacy = runtime_dir / f"{index}-{keyword}.json"
    if legacy.parent == runtime_dir and legacy.exists():
        return legacy
    slug = re.sub(r"[^\w.-]+", "-", keyword, flags=re.UNICODE).strip("-._")
    slug = (slug or "keyword")[:64]
    digest = hashlib.sha1(keyword.encode("utf-8")).hexdigest()[:10]
    return runtime_dir / f"{index}-{slug}-{digest}.json"


def classify_video(title: str, matched_keywords: list[str]) -> tuple[str, str, str]:
    normalized = title.lower()
    direct_hits = [term for term in DIRECT_TITLE_TERMS if term in normalized]
    if direct_hits:
        return (
            "强相关",
            "VPN/翻墙",
            "标题直接出现 " + "、".join(direct_hits),
        )
    if "加速器" in normalized:
        return "强相关", "加速器", "标题直接出现加速器"
    overseas_hits = [term for term in OVERSEAS_TERMS if term in normalized]
    if overseas_hits:
        return (
            "场景相关",
            "海外访问/跨境网络",
            "标题涉及 " + "、".join(overseas_hits[:4]),
        )
    game_hits = [term for term in GAME_NETWORK_TERMS if term in normalized]
    if game_hits:
        return (
            "场景相关",
            "游戏网络/延迟",
            "标题涉及 " + "、".join(game_hits[:4]),
        )
    if any(term in normalized for term in ("网络", "服务器", "ip", "wifi", "宽带")):
        return "场景相关", "网络相关", "标题涉及网络、服务器、IP、WiFi或宽带"
    return (
        "弱相关",
        "搜索关联",
        "由关键词“" + "、".join(matched_keywords) + "”的搜索接口返回",
    )


def _clean_cell(value: Any) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _japan_time(value: str | None) -> str:
    if not value:
        return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S")
    try:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            .astimezone(ZoneInfo("Asia/Tokyo"))
            .strftime("%Y-%m-%d %H:%M:%S")
        )
    except ValueError:
        return value


def merge_video(
    aggregate: dict[str, dict[str, Any]],
    video: dict[str, Any],
    keyword: str,
    keyword_order: dict[str, int],
) -> None:
    video_id = str(video.get("video_id") or "").strip()
    video_url = str(video.get("video_url") or "").strip()
    if not video_id or not video_url:
        return
    existing = aggregate.get(video_id)
    if existing is None:
        existing = next(
            (
                row
                for row in aggregate.values()
                if str(row.get("video_url") or "").strip() == video_url
            ),
            None,
        )
    if existing is None:
        aggregate[video_id] = {
            "video_id": video_id,
            "video_url": video_url,
            "title": str(video.get("title") or "").strip(),
            "author_name": str(video.get("author_name") or "").strip(),
            "keywords": {keyword},
            "first_seen_at": video.get("first_seen_at"),
        }
        return
    existing["keywords"].add(keyword)
    if not existing["title"] and video.get("title"):
        existing["title"] = str(video["title"]).strip()
    if not existing["author_name"] and video.get("author_name"):
        existing["author_name"] = str(video["author_name"]).strip()
    incoming_seen = str(video.get("first_seen_at") or "")
    existing_seen = str(existing.get("first_seen_at") or "")
    if incoming_seen and (not existing_seen or incoming_seen < existing_seen):
        existing["first_seen_at"] = incoming_seen


def build_markdown_records(
    aggregate: dict[str, dict[str, Any]], keyword_order: dict[str, int]
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for video in aggregate.values():
        matched = sorted(
            set(video.get("keywords") or []),
            key=lambda word: keyword_order.get(word, 9999),
        )
        relevance, category, basis = classify_video(
            str(video.get("title") or ""), matched
        )
        records.append(
            {
                "相关度": relevance,
                "内容分类": category,
                "匹配依据": basis,
                "关键词": "、".join(matched),
                "标题": str(video.get("title") or ""),
                "作者": str(video.get("author_name") or ""),
                "视频链接": str(video.get("video_url") or ""),
                "链接来源": "搜索接口 /rest/v/search/feed",
                "采集时间": _japan_time(video.get("first_seen_at")),
            }
        )
    relevance_order = {"强相关": 0, "场景相关": 1, "弱相关": 2}
    records.sort(
        key=lambda row: (
            relevance_order.get(row["相关度"], 9),
            keyword_order.get(row["关键词"].split("、")[0], 9999),
            row["标题"],
            row["视频链接"],
        )
    )
    return records


def render_markdown(
    output_path: Path,
    keyword_rows: list[dict[str, str]],
    metrics: list[dict[str, Any]],
    aggregate: dict[str, dict[str, Any]],
    started_at: str,
    ended_at: str,
    requested_loops: int,
    min_interval: float = 6.0,
    max_interval: float = 12.0,
    max_consecutive: int = 5,
    auto_reset_identity: bool = False,
) -> None:
    keyword_order = {row["keyword"]: index for index, row in enumerate(keyword_rows)}
    raw_hits = sum(int(metric.get("raw_feed_rows") or 0) for metric in metrics)
    per_keyword_unique = sum(int(metric.get("unique_videos") or 0) for metric in metrics)
    successful = sum(int(metric.get("successful_rounds") or 0) for metric in metrics)
    failed = sum(int(metric.get("failed_rounds") or 0) for metric in metrics)
    identity_resets = sum(int(metric.get("identity_resets") or 0) for metric in metrics)
    records = build_markdown_records(aggregate, keyword_order)

    lines = [
        f"# 快手视频链接（{len(keyword_rows)}个关键词匿名交错采样）",
        "",
        f"- 采集开始：{_japan_time(started_at)}",
        f"- 采集结束：{_japan_time(ended_at)}",
        f"- 模式：未登录；TSV前 {len(keyword_rows)} 个关键词；每词总计 {requested_loops} 次",
        f"- 调度：同一关键词每批随机 1–{max_consecutive} 次；搜索间隔随机 {min_interval:g}–{max_interval:g} 秒",
        f"- 自动深度重置：{'开启' if auto_reset_identity else '关闭（异常时暂停等待人工处理）'}",
        "- 搜索入口：首页搜索框输入关键词并点击“搜索”；不直接打开 /search/{keyword}。",
        f"- 搜索成功轮次：{successful}",
        f"- 搜索失败轮次：{failed}",
        f"- 网络异常后全量身份重置：{identity_resets} 次",
        f"- 搜索接口原始视频命中：{raw_hits}",
        f"- 各关键词内去重后合计：{per_keyword_unique}",
        f"- 全局按视频ID/链接去重后：{len(records)}",
        "- 两阶段处理：第一阶段只累计视频ID/链接；第二阶段去重后从已捕获响应补齐标题和作者。",
        "",
        "## 关键词采样统计",
        "",
        "| 关键词 | 计划轮次 | 实际轮次 | 成功轮次 | 失败轮次 | 身份重置 | 原始命中 | 词内去重 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for metric in metrics:
        lines.append(
            "| "
            + " | ".join(
                _clean_cell(value)
                for value in (
                    metric["keyword"],
                    requested_loops,
                    _metric_rounds(metric),
                    metric["successful_rounds"],
                    metric["failed_rounds"],
                    metric.get("identity_resets", 0),
                    metric["raw_feed_rows"],
                    metric["unique_videos"],
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 去重后视频",
            "",
            "| 相关度 | 内容分类 | 匹配依据 | 关键词 | 标题 | 作者 | 视频链接 | 链接来源 | 采集时间 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    columns = [
        "相关度",
        "内容分类",
        "匹配依据",
        "关键词",
        "标题",
        "作者",
        "视频链接",
        "链接来源",
        "采集时间",
    ]
    for record in records:
        lines.append(
            "| " + " | ".join(_clean_cell(record[column]) for column in columns) + " |"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_master_record(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if not path.exists():
        return {}, {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    aggregate: dict[str, dict[str, Any]] = {}
    for video in payload.get("videos") or []:
        restored = dict(video)
        restored["keywords"] = set(restored.get("keywords") or [])
        video_id = str(restored.get("video_id") or "").strip()
        if video_id:
            aggregate[video_id] = restored
    sources = {
        str(row["run_id"]): dict(row)
        for row in payload.get("source_runs") or []
        if row.get("run_id")
    }
    return aggregate, sources


def merge_into_master(
    master: dict[str, dict[str, Any]],
    videos: list[dict[str, Any]],
    keyword_order: dict[str, int],
) -> None:
    for video in videos:
        keywords = list(video.get("keywords") or video.get("matched_keywords") or [])
        if not keywords:
            keywords = ["未标记"]
        for keyword in keywords:
            merge_video(master, video, str(keyword), keyword_order)


def save_master_record(
    path: Path,
    master: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    videos = [
        {**video, "keywords": sorted(video.get("keywords") or [])}
        for video in sorted(master.values(), key=lambda row: row["video_id"])
    ]
    _atomic_json(
        path,
        {
            "schema_version": 1,
            "updated_at": now,
            "dedupe_keys": ["video_id", "video_url"],
            "video_count": len(videos),
            "source_runs": [sources[key] for key in sorted(sources)],
            "videos": videos,
        },
    )


def render_master_markdown(
    output_path: Path,
    master: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    keyword_order: dict[str, int],
) -> None:
    records = build_markdown_records(master, keyword_order)
    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 快手视频链接（累计去重总表）",
        "",
        f"- 最后更新：{now}",
        f"- 数据批次：{len(sources)}",
        f"- 全局按视频ID/链接去重：{len(records)} 条",
        "- 本文件为后续采集持续更新的唯一累计记录。",
        "- 字段与 `快手视频链接.xlsx` 保持一致。",
        "",
        "## 数据批次",
        "",
        "| 批次 | 批次内去重链接 | 状态 |",
        "|---|---:|---|",
    ]
    for run_id in sorted(sources):
        source = sources[run_id]
        lines.append(
            "| "
            + " | ".join(
                _clean_cell(value)
                for value in (
                    run_id,
                    source.get("unique_video_count", ""),
                    source.get("status", "recorded"),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 去重后视频",
            "",
            "| 相关度 | 内容分类 | 匹配依据 | 关键词 | 标题 | 作者 | 视频链接 | 链接来源 | 采集时间 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    columns = [
        "相关度",
        "内容分类",
        "匹配依据",
        "关键词",
        "标题",
        "作者",
        "视频链接",
        "链接来源",
        "采集时间",
    ]
    for record in records:
        lines.append(
            "| " + " | ".join(_clean_cell(record[column]) for column in columns) + " |"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(output_path)


def merge_keyword_videos(
    aggregate: dict[str, dict[str, Any]], videos: list[dict[str, Any]]
) -> None:
    for video in videos:
        video_id = str(video.get("video_id") or "").strip()
        video_url = str(video.get("video_url") or "").strip()
        if not video_id or not video_url:
            continue
        existing = aggregate.get(video_id)
        if existing is None:
            existing = next(
                (
                    row
                    for row in aggregate.values()
                    if str(row.get("video_url") or "").strip() == video_url
                ),
                None,
            )
        if existing is None:
            aggregate[video_id] = dict(video)
            continue
        for field in ("title", "author_name", "video_url", "first_seen_at"):
            if not existing.get(field) and video.get(field):
                existing[field] = video[field]


def load_checkpoint(path: Path, keyword: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "keyword": keyword,
            "runs": [],
            "successful_rounds": 0,
            "failed_rounds": 0,
            "raw_feed_rows": 0,
            "identity_resets": 0,
            "last_event": None,
            "videos": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("keyword") != keyword:
        raise ValueError(f"checkpoint keyword mismatch: {path}")
    payload.setdefault("runs", [])
    payload.setdefault(
        "successful_rounds",
        sum(bool(row.get("successful")) for row in payload["runs"]),
    )
    payload.setdefault(
        "failed_rounds",
        sum(not bool(row.get("successful")) for row in payload["runs"]),
    )
    payload.setdefault("raw_feed_rows", 0)
    payload.setdefault("identity_resets", 0)
    payload.setdefault("last_event", None)
    payload.setdefault("videos", [])
    return payload


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _row_has_risk(row: dict[str, Any]) -> bool:
    """Read both the current flag and the legacy list-shaped checkpoint field."""
    return bool(row.get("risk_control") or row.get("risk_controls"))


def _counted_rounds(runs: list[dict[str, Any]]) -> int:
    """Count real search navigations; legacy rows were all real searches."""
    return sum(bool(row.get("counted_as_round", True)) for row in runs)


def _needs_identity_reset(state: dict[str, Any]) -> bool:
    runs = list(state.get("runs") or [])
    if not runs:
        return False
    last = runs[-1]
    return bool(
        not last.get("successful")
        and last.get("counted_as_round", True)
        and not last.get("identity_reset_handled")
        and state.get("last_event") != "identity_reset"
    )


def _metric_rounds(metric: dict[str, Any]) -> int:
    if metric.get("completed_rounds") is not None:
        return int(metric["completed_rounds"])
    if metric.get("attempted_rounds") is not None:
        return int(metric["attempted_rounds"])
    return int(metric.get("successful_rounds") or 0) + int(
        metric.get("failed_rounds") or 0
    )


def checkpoint_metric(payload: dict[str, Any]) -> dict[str, Any]:
    videos = payload.get("videos") or []
    runs = payload.get("runs") or []
    return {
        "keyword": payload["keyword"],
        "attempted_rounds": len(runs),
        "completed_rounds": _counted_rounds(runs),
        "successful_rounds": int(payload.get("successful_rounds") or 0),
        "failed_rounds": int(payload.get("failed_rounds") or 0),
        "raw_feed_rows": int(payload.get("raw_feed_rows") or 0),
        "unique_videos": len(videos),
        "risk_control_count": sum(_row_has_risk(row) for row in runs),
        "identity_resets": int(payload.get("identity_resets") or 0),
    }


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def save_runtime_snapshot(
    runtime_dir: Path,
    *,
    started_at: str,
    keyword_rows: list[dict[str, str]],
    keyword_position: int,
    metric: dict[str, Any],
    metrics: list[dict[str, Any]],
    aggregate: dict[str, dict[str, Any]],
    requested_loops: int,
    event: str,
    details: dict[str, Any] | None = None,
    min_interval: float = 6.0,
    max_interval: float = 12.0,
    max_consecutive: int = 5,
    auto_reset_identity: bool = False,
) -> None:
    """Atomically record links and exact resume position before any reset."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    serialized_videos = [
        {**video, "keywords": sorted(video["keywords"])}
        for video in sorted(aggregate.values(), key=lambda row: row["video_id"])
    ]
    completed = sum(_metric_rounds(row) >= requested_loops for row in metrics)
    phase = {
        "run_complete": "completed",
        "user_paused": "paused",
        "manual_reset_required": "paused",
        "run_interrupted": "interrupted",
    }.get(event, "collect_links")
    progress = {
        "schema_version": 1,
        "phase": phase,
        "started_at": started_at,
        "updated_at": now,
        "event": event,
        "current_keyword_position": keyword_position,
        "keyword_count": len(keyword_rows),
        "current_keyword_index": keyword_rows[keyword_position - 1]["keyword_index"],
        "current_keyword": metric["keyword"],
        "current_attempt": metric["attempted_rounds"],
        "current_completed_rounds": _metric_rounds(metric),
        "current_successful_rounds": metric["successful_rounds"],
        "target_search_rounds": requested_loops,
        "schedule": {
            "min_interval_seconds": min_interval,
            "max_interval_seconds": max_interval,
            "max_consecutive_per_keyword": max_consecutive,
            "auto_reset_identity": auto_reset_identity,
        },
        "resume_from_round": min(
            _metric_rounds(metric) + 1,
            requested_loops,
        ),
        "completed_keywords": completed,
        "global_unique_links": len(serialized_videos),
        "total_identity_resets": sum(
            int(row.get("identity_resets") or 0) for row in metrics
        ),
        "details": details or {},
    }
    _atomic_json(
        runtime_dir / "aggregate.json",
        {
            "started_at": started_at,
            "updated_at": now,
            "metrics": metrics,
            "videos": serialized_videos,
        },
    )
    _atomic_json(
        runtime_dir / "deduped_links.json",
        {
            "updated_at": now,
            "count": len(serialized_videos),
            "videos": serialized_videos,
        },
    )
    _atomic_json(runtime_dir / "progress.json", progress)
    with (runtime_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(progress, ensure_ascii=False) + "\n")


class Sampler:
    def __init__(self, base_url: str, timeout: float):
        self.client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def _json(self, method: str, path: str, payload: dict | None = None) -> dict:
        response = self.client.request(method, path, json=payload)
        response.raise_for_status()
        return response.json()

    def _navigate_anonymous_home(self) -> dict:
        try:
            return self._json(
                "POST",
                "/api/browser/navigate",
                {"url": "https://www.kuaishou.com/?isHome=1&source=SEARCH"},
            )
        except httpx.HTTPStatusError:
            # A commit-level navigation can time out after Kuaishou has already
            # rendered a usable page. Trust the browser status in that case.
            status = self._json("GET", "/api/probe/status")
            if status.get("browser_state") != "running":
                raise
            return status

    def _restart_stale_browser(self) -> dict:
        """Restart the browser process while retaining its identity/Profile."""
        print(
            "[browser-recovery] login-status page was stale; "
            "restarting the same persisted identity",
            flush=True,
        )
        try:
            self._json("POST", "/api/browser/close", {})
        except httpx.HTTPError:
            # close_browser clears manager state in a finally block even when
            # Playwright reports that the old process has already disappeared.
            pass
        return self._navigate_anonymous_home()

    def ensure_anonymous_browser(self) -> dict:
        status = self._json("GET", "/api/probe/status")
        if status.get("probe_state") in {"starting", "running", "error"}:
            self._json("POST", "/api/probe/stop", {})
        if status.get("browser_state") != "running":
            self._navigate_anonymous_home()
        status = self._json("GET", "/api/probe/status")
        if str(status.get("page_url") or "").startswith("https://www.kuaishou.com"):
            try:
                login = self._json("GET", "/api/browser/login-status")
            except httpx.HTTPStatusError as exc:
                response = exc.response
                if response is None or response.status_code not in {
                    409,
                    500,
                    502,
                    503,
                    504,
                }:
                    raise
                self._restart_stale_browser()
                login = self._json("GET", "/api/browser/login-status")
            if login.get("logged_in"):
                raise RuntimeError("当前浏览器已登录；本任务要求匿名 Profile")
            return login
        return {"logged_in": False, "check_login": None, "deferred": True}

    def reset_anonymous_identity(self) -> dict[str, Any]:
        """Clear the full persistent profile and verify the new one is anonymous."""
        response = self._json("POST", "/api/browser/reset-identity", {})
        login = self._json("GET", "/api/browser/login-status")
        if login.get("logged_in"):
            raise RuntimeError("身份重置后浏览器意外处于登录状态")
        reset = dict(response.get("identity_reset") or {})
        reset["anonymous_verified"] = True
        return reset

    def wait_first_response(self, timeout_seconds: float = 25.0) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self._json("GET", "/api/probe/status")
            extracted = (status.get("probe") or {}).get("extracted") or {}
            total = int(extracted.get("successful_search_responses") or 0) + int(
                extracted.get("failed_search_responses") or 0
            )
            if total >= 1:
                return status
            time.sleep(0.2)
        raise TimeoutError("首轮搜索接口响应超时")

    def sample_keyword(
        self,
        keyword: str,
        loops: int,
        min_interval: float,
        max_interval: float,
        checkpoint: Path,
        max_attempts: int,
        batch_size: int | None,
        auto_reset_identity: bool,
        on_checkpoint: Callable[
            [dict[str, Any], list[dict[str, Any]], str, dict[str, Any] | None],
            None,
        ],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        state = load_checkpoint(checkpoint, keyword)
        successful = int(state["successful_rounds"])
        failed = int(state["failed_rounds"])
        raw_feed_rows = int(state["raw_feed_rows"])
        identity_resets = int(state.get("identity_resets") or 0)
        runs = list(state["runs"])
        prior_last_event = state.get("last_event")
        keyword_videos: dict[str, dict[str, Any]] = {}
        merge_keyword_videos(keyword_videos, list(state["videos"]))

        def persist(event: str, details: dict[str, Any] | None = None) -> None:
            nonlocal state
            state = {
                "schema_version": 2,
                "keyword": keyword,
                "runs": runs,
                "successful_rounds": successful,
                "failed_rounds": failed,
                "raw_feed_rows": raw_feed_rows,
                "identity_resets": identity_resets,
                "last_event": event,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "videos": list(keyword_videos.values()),
            }
            save_checkpoint(checkpoint, state)
            on_checkpoint(
                checkpoint_metric(state),
                list(keyword_videos.values()),
                event,
                details,
            )

        probe_active = False
        seen_success = 0
        seen_failed = 0
        seen_raw = 0
        seen_risks = 0

        def stop_probe() -> None:
            nonlocal probe_active
            try:
                status = self._json("GET", "/api/probe/status")
                if status.get("probe_state") in {"starting", "running", "error"}:
                    self._json("POST", "/api/probe/stop", {})
            except Exception:
                pass
            probe_active = False

        def reset_after_error(error_details: dict[str, Any]) -> None:
            nonlocal identity_resets
            stop_probe()
            last_exception: Exception | None = None
            for reset_attempt in range(1, 4):
                try:
                    reset = self.reset_anonymous_identity()
                    identity_resets += 1
                    if runs:
                        runs[-1]["identity_reset_handled"] = True
                        runs[-1]["identity_reset_at"] = datetime.now(
                            timezone.utc
                        ).isoformat(timespec="seconds")
                    persist(
                        "identity_reset",
                        {
                            **error_details,
                            "reset_attempt": reset_attempt,
                            "identity_reset": reset,
                        },
                    )
                    return
                except Exception as exc:
                    last_exception = exc
                    persist(
                        "identity_reset_failed",
                        {
                            **error_details,
                            "reset_attempt": reset_attempt,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    time.sleep(random.uniform(min_interval, max_interval))
            assert last_exception is not None
            raise last_exception

        persist("resume_loaded")
        # The previous process was stopped after a failed response. Its links
        # are persisted above; finish that pending reset before deciding whether
        # the keyword's 20 real search rounds are already complete.
        state_for_pending_check = {**state, "last_event": prior_last_event}
        if _needs_identity_reset(state_for_pending_check):
            persist(
                "network_error_checkpointed",
                {"reason": "resume_after_unhandled_previous_error"},
            )
            if auto_reset_identity:
                reset_after_error(
                    {"reason": "resume_after_unhandled_previous_error"}
                )
            else:
                persist(
                    "manual_reset_required",
                    {"reason": "resume_after_unhandled_previous_error"},
                )
                raise ManualResetRequired(
                    f"{keyword} 上次搜索异常，请在 Web 控制台执行“深度重置身份”后继续"
                )

        if _counted_rounds(runs) >= loops:
            persist("keyword_complete")
            return checkpoint_metric(state), list(keyword_videos.values())

        current_rounds = _counted_rounds(runs)
        batch_target = min(loops, current_rounds + (batch_size or loops))
        persist(
            "batch_started",
            {
                "batch_size": batch_target - current_rounds,
                "batch_start_round": current_rounds + 1,
                "batch_end_round": batch_target,
            },
        )

        try:
            while _counted_rounds(runs) < batch_target:
                if max_attempts > 0 and len(runs) >= max_attempts:
                    persist("attempt_limit_reached")
                    raise RuntimeError(
                        f"{keyword} 达到最大尝试次数 {max_attempts}，已保存续跑点"
                    )

                delay = random.uniform(min_interval, max_interval)
                time.sleep(delay)
                status: dict[str, Any] | None = None
                error: str | None = None
                search_page_verified = False
                try:
                    if not probe_active:
                        started = self._json(
                            "POST", "/api/probe/start", {"keyword": keyword}
                        )
                        probe_active = True
                        seen_success = seen_failed = seen_raw = seen_risks = 0
                        expected_url = "https://www.kuaishou.com/search/" + quote(
                            keyword, safe=""
                        )
                        navigation = started.get("search_navigation") or {}
                        if (
                            not navigation.get("verified")
                            or not str(started.get("page_url") or "").startswith(
                                expected_url
                            )
                        ):
                            raise RuntimeError(
                                "search navigation was not visibly committed: "
                                + str(started.get("page_url"))
                            )
                        search_page_verified = True
                        status = self.wait_first_response()
                    else:
                        self._json("POST", "/api/probe/repeat-search", {})
                        search_page_verified = True
                        status = self._json("GET", "/api/probe/status")
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    try:
                        status = self._json("GET", "/api/probe/status")
                    except Exception:
                        status = None

                probe = (status or {}).get("probe") or {}
                extracted = probe.get("extracted") or {}
                current_success = int(extracted.get("successful_search_responses") or 0)
                current_failed = int(extracted.get("failed_search_responses") or 0)
                current_raw = int(extracted.get("search_feed_rows") or 0)
                current_risks = len(probe.get("risk_controls") or [])
                success_delta = max(0, current_success - seen_success)
                failed_delta = max(0, current_failed - seen_failed)
                raw_delta = max(0, current_raw - seen_raw)
                risk_delta = max(0, current_risks - seen_risks)
                seen_success = current_success
                seen_failed = current_failed
                seen_raw = current_raw
                seen_risks = current_risks
                merge_keyword_videos(keyword_videos, list(extracted.get("videos") or []))

                response_observed = success_delta > 0 or failed_delta > 0
                counted_as_round = search_page_verified and response_observed
                attempt_successful = (
                    success_delta > 0 and failed_delta == 0 and not error
                )
                if attempt_successful:
                    successful += 1
                else:
                    failed += 1
                    error = error or "search_response_failed"
                raw_feed_rows += raw_delta
                row = {
                    "attempt": len(runs) + 1,
                    "successful": attempt_successful,
                    "counted_as_round": counted_as_round,
                    "keyword_search_count": _counted_rounds(runs)
                    + int(counted_as_round),
                    "delay_seconds": round(delay, 3),
                    "risk_control": risk_delta > 0,
                    "error": error,
                    "successful_rounds": successful,
                    "raw_feed_rows": raw_feed_rows,
                    "unique_videos": len(keyword_videos),
                    "recorded_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                }
                runs.append(row)
                if attempt_successful:
                    event = "search_success"
                elif counted_as_round:
                    event = "network_error_checkpointed"
                else:
                    event = "search_response_retry"
                persist(
                    event,
                    {
                        "error": error,
                        "risk_control": row["risk_control"],
                        "response_observed": response_observed,
                        "delay_seconds": row["delay_seconds"],
                    },
                )
                print(
                    f"[{keyword}] round={_counted_rounds(runs)}/{loops} "
                    f"success={successful} failed={failed} "
                    f"attempt={len(runs)}/{max_attempts or '∞'} "
                    f"delay={delay:.2f}s raw={raw_feed_rows} "
                    f"unique={len(keyword_videos)} risk={row['risk_control']}",
                    flush=True,
                )

                if not attempt_successful and counted_as_round:
                    reset_details = {
                        "reason": error,
                        "risk_control": row["risk_control"],
                        "attempt": row["attempt"],
                    }
                    if auto_reset_identity:
                        reset_after_error(reset_details)
                    else:
                        stop_probe()
                        persist("manual_reset_required", reset_details)
                        raise ManualResetRequired(
                            f"{keyword} 搜索异常，自动深度重置已关闭"
                        )
                elif not attempt_successful:
                    # A page/navigation timeout with no feed response is not a
                    # Kuaishou business rejection. Do not count it as one of the
                    # 20 searches and do not request an identity reset.
                    stop_probe()
                    persist(
                        "search_retry_without_reset",
                        {
                            "reason": error,
                            "attempt": row["attempt"],
                            "search_page_verified": search_page_verified,
                            "response_observed": response_observed,
                            "window_kept_open": True,
                        },
                    )

            persist(
                "keyword_complete"
                if _counted_rounds(runs) >= loops
                else "batch_complete"
            )
            return checkpoint_metric(state), list(keyword_videos.values())
        finally:
            stop_probe()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--keywords",
        type=Path,
        default=Path("resources/keywords/vpn_kuaishou_search_keywords.tsv"),
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--loops", type=int, default=20)
    parser.add_argument("--min-interval", type=float, default=6.0)
    parser.add_argument("--max-interval", type=float, default=12.0)
    parser.add_argument("--max-consecutive", type=int, default=5)
    parser.add_argument(
        "--auto-reset-identity",
        action="store_true",
        help="deep-reset the browser automatically after a verified search failure",
    )
    parser.add_argument(
        "--allow-visible-browser",
        action="store_true",
        help="allow a headed browser that can take desktop focus",
    )
    parser.add_argument("--max-attempts", type=int, default=250)
    parser.add_argument("--resume-dir", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--master-json",
        type=Path,
        default=Path("runtime/sampling/master/deduped_links.json"),
    )
    parser.add_argument(
        "--master-output",
        type=Path,
        default=Path(
            "outputs/kuaishou-anonymous-sample-20260908/"
            "快手视频链接_累计去重总表.md"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "outputs/kuaishou-anonymous-sample-20260908/"
            "全部关键词_每词20次_累计去重结果.md"
        ),
    )
    args = parser.parse_args()
    if args.loops < 1:
        raise ValueError("loops must be positive")
    if args.min_interval < 0 or args.max_interval > 12:
        raise ValueError("random interval must stay between 0 and 12 seconds")
    if args.min_interval > args.max_interval:
        raise ValueError("min interval must be <= max interval")
    if not 1 <= args.max_consecutive <= 5:
        raise ValueError("max consecutive searches must be between 1 and 5")
    keyword_rows = load_keywords(args.keywords, args.limit)
    keyword_order = {row["keyword"]: index for index, row in enumerate(keyword_rows)}
    all_keyword_rows = load_keywords(args.keywords, 1_000_000)
    master_keyword_order = {
        row["keyword"]: index for index, row in enumerate(all_keyword_rows)
    }
    session_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    runtime_dir = args.resume_dir or (Path("runtime/sampling") / session_stamp)
    aggregate: dict[str, dict[str, Any]] = {}
    metrics: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    aggregate_path = runtime_dir / "aggregate.json"
    if aggregate_path.exists():
        saved = json.loads(aggregate_path.read_text(encoding="utf-8"))
        metrics = list(saved.get("metrics") or [])
        started_at = saved.get("started_at") or started_at
        for video in saved.get("videos") or []:
            restored = dict(video)
            restored["keywords"] = set(restored.get("keywords") or [])
            aggregate[restored["video_id"]] = restored
    master, master_sources = load_master_record(args.master_json)

    sampler = Sampler(args.base_url, timeout=90)
    current_position = 1
    progress_path = runtime_dir / "progress.json"
    if progress_path.exists():
        try:
            previous_progress = json.loads(progress_path.read_text(encoding="utf-8"))
            current_position = max(
                1,
                min(
                    len(keyword_rows),
                    int(previous_progress.get("current_keyword_position") or 1),
                ),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            current_position = 1
    run_error: Exception | None = None

    def record_checkpoint(
        position: int,
        keyword: str,
        metric: dict[str, Any],
        videos: list[dict[str, Any]],
        event: str,
        details: dict[str, Any] | None,
    ) -> None:
        nonlocal metrics
        for video in videos:
            merge_video(aggregate, video, keyword, keyword_order)
        metrics = [row for row in metrics if row.get("keyword") != keyword]
        metrics.append(metric)
        metrics.sort(key=lambda row: keyword_order.get(row["keyword"], 9999))
        save_runtime_snapshot(
            runtime_dir,
            started_at=started_at,
            keyword_rows=keyword_rows,
            keyword_position=position,
            metric=metric,
            metrics=metrics,
            aggregate=aggregate,
            requested_loops=args.loops,
            event=event,
            details=details,
            min_interval=args.min_interval,
            max_interval=args.max_interval,
            max_consecutive=args.max_consecutive,
            auto_reset_identity=args.auto_reset_identity,
        )
        merge_into_master(master, videos, master_keyword_order)
        master_sources[runtime_dir.name] = {
            "run_id": runtime_dir.name,
            "path": str(runtime_dir),
            "unique_video_count": len(aggregate),
            "status": "active",
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        save_master_record(args.master_json, master, master_sources)
        if event in {
            "search_success",
            "network_error_checkpointed",
            "batch_complete",
            "keyword_complete",
        }:
            render_master_markdown(
                args.master_output, master, master_sources, master_keyword_order
            )

    def mark_master_status(status: str) -> None:
        master_sources[runtime_dir.name] = {
            "run_id": runtime_dir.name,
            "path": str(runtime_dir),
            "unique_video_count": len(aggregate),
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        save_master_record(args.master_json, master, master_sources)
        render_master_markdown(
            args.master_output, master, master_sources, master_keyword_order
        )

    try:
        service_status = sampler._json("GET", "/api/probe/status")
        if not service_status.get("headless") and not args.allow_visible_browser:
            raise RuntimeError(
                "自动采集要求后台无窗口模式；请以 "
                "KUAISHOU_BROWSER_HEADLESS=true 启动服务"
            )
        login = sampler.ensure_anonymous_browser()
        print(f"anonymous={not login['logged_in']}", flush=True)
        scheduler_cycle = 0
        while True:
            pending: list[tuple[int, dict[str, str], Path, dict[str, Any]]] = []
            for position, keyword_row in enumerate(keyword_rows, start=1):
                keyword = keyword_row["keyword"]
                checkpoint_path = keyword_checkpoint_path(runtime_dir, keyword_row)
                checkpoint_state = load_checkpoint(checkpoint_path, keyword)
                pending_error_reset = _needs_identity_reset(checkpoint_state)
                if (
                    _counted_rounds(checkpoint_state["runs"]) < args.loops
                    or pending_error_reset
                ):
                    pending.append(
                        (position, keyword_row, checkpoint_path, checkpoint_state)
                    )
            if not pending:
                break

            scheduler_cycle += 1
            for position, keyword_row, checkpoint_path, checkpoint_state in pending:
                current_position = position
                keyword = keyword_row["keyword"]
                completed_before = _counted_rounds(checkpoint_state["runs"])
                remaining = max(0, args.loops - completed_before)
                batch_size = min(
                    remaining,
                    random.randint(1, args.max_consecutive),
                )
                print(
                    f"[scheduler] cycle={scheduler_cycle} keyword={keyword} "
                    f"completed={completed_before}/{args.loops} batch={batch_size}",
                    flush=True,
                )

                def batch_checkpoint(
                    metric: dict[str, Any],
                    videos: list[dict[str, Any]],
                    event: str,
                    details: dict[str, Any] | None,
                    *,
                    position: int = position,
                    keyword: str = keyword,
                    scheduler_cycle: int = scheduler_cycle,
                    batch_size: int = batch_size,
                ) -> None:
                    record_checkpoint(
                        position,
                        keyword,
                        metric,
                        videos,
                        event,
                        {
                            "scheduler_cycle": scheduler_cycle,
                            "scheduled_batch_size": batch_size,
                            **(details or {}),
                        },
                    )

                sampler.sample_keyword(
                    keyword,
                    args.loops,
                    args.min_interval,
                    args.max_interval,
                    checkpoint_path,
                    args.max_attempts,
                    batch_size,
                    args.auto_reset_identity,
                    batch_checkpoint,
                )
        if metrics:
            last_metric = metrics[-1]
            save_runtime_snapshot(
                runtime_dir,
                started_at=started_at,
                keyword_rows=keyword_rows,
                keyword_position=len(keyword_rows),
                metric=last_metric,
                metrics=metrics,
                aggregate=aggregate,
                requested_loops=args.loops,
                event="run_complete",
                min_interval=args.min_interval,
                max_interval=args.max_interval,
                max_consecutive=args.max_consecutive,
                auto_reset_identity=args.auto_reset_identity,
            )
            mark_master_status("completed")
    except KeyboardInterrupt:
        if metrics:
            last_metric = next(
                (
                    row
                    for row in metrics
                    if row.get("keyword")
                    == keyword_rows[current_position - 1]["keyword"]
                ),
                metrics[-1],
            )
            save_runtime_snapshot(
                runtime_dir,
                started_at=started_at,
                keyword_rows=keyword_rows,
                keyword_position=current_position,
                metric=last_metric,
                metrics=metrics,
                aggregate=aggregate,
                requested_loops=args.loops,
                event="user_paused",
                details={"reason": "keyboard_interrupt"},
                min_interval=args.min_interval,
                max_interval=args.max_interval,
                max_consecutive=args.max_consecutive,
                auto_reset_identity=args.auto_reset_identity,
            )
            mark_master_status("paused")
        print("sampling paused; checkpoints saved", flush=True)
    except ManualResetRequired as exc:
        if metrics:
            last_metric = next(
                (
                    row
                    for row in metrics
                    if row.get("keyword")
                    == keyword_rows[current_position - 1]["keyword"]
                ),
                metrics[-1],
            )
            save_runtime_snapshot(
                runtime_dir,
                started_at=started_at,
                keyword_rows=keyword_rows,
                keyword_position=current_position,
                metric=last_metric,
                metrics=metrics,
                aggregate=aggregate,
                requested_loops=args.loops,
                event="manual_reset_required",
                details={"error": str(exc)},
                min_interval=args.min_interval,
                max_interval=args.max_interval,
                max_consecutive=args.max_consecutive,
                auto_reset_identity=False,
            )
            mark_master_status("paused")
        print(f"sampling paused; manual reset required: {exc}", flush=True)
    except Exception as exc:
        run_error = exc
        if metrics:
            last_metric = next(
                (
                    row
                    for row in metrics
                    if row.get("keyword") == keyword_rows[current_position - 1]["keyword"]
                ),
                metrics[-1],
            )
            save_runtime_snapshot(
                runtime_dir,
                started_at=started_at,
                keyword_rows=keyword_rows,
                keyword_position=current_position,
                metric=last_metric,
                metrics=metrics,
                aggregate=aggregate,
                requested_loops=args.loops,
                event="run_interrupted",
                details={"error": f"{type(exc).__name__}: {exc}"},
                min_interval=args.min_interval,
                max_interval=args.max_interval,
                max_consecutive=args.max_consecutive,
                auto_reset_identity=args.auto_reset_identity,
            )
            mark_master_status("interrupted")
    finally:
        sampler.close()

        ended_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        render_markdown(
            args.output,
            keyword_rows,
            metrics,
            aggregate,
            started_at,
            ended_at,
            args.loops,
            args.min_interval,
            args.max_interval,
            args.max_consecutive,
            args.auto_reset_identity,
        )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "keywords": len(keyword_rows),
                "loops_per_keyword": args.loops,
                "raw_feed_rows": sum(row["raw_feed_rows"] for row in metrics),
                "global_unique_videos": len(aggregate),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if run_error is not None:
        raise run_error


if __name__ == "__main__":
    main()
