#!/usr/bin/env python3
"""Resolve comment totals for the cumulative deduplicated Kuaishou videos."""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts.sample_kuaishou_top_keywords import (
    _clean_cell,
    _japan_time,
    build_markdown_records,
    load_keywords,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def load_source(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    videos = [dict(video) for video in payload.get("videos") or []]
    if int(payload.get("video_count") or len(videos)) != len(videos):
        raise ValueError("master video_count does not match its video rows")
    ids = [str(video.get("video_id") or "") for video in videos]
    urls = [str(video.get("video_url") or "") for video in videos]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("master source contains missing or duplicate video IDs")
    if not all(urls) or len(urls) != len(set(urls)):
        raise ValueError("master source contains missing or duplicate video URLs")
    return videos, payload.get("updated_at")


def load_checkpoint(path: Path, source: Path) -> dict[str, Any]:
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("results", {})
        payload.setdefault("started_at", utc_now())
        payload.setdefault("phase", "running")
        return payload
    return {
        "schema_version": 1,
        "phase": "running",
        "event": "created",
        "source": str(source),
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "threshold": 50,
        "results": {},
    }


def checkpoint_stats(
    checkpoint: dict[str, Any], source_videos: list[dict[str, Any]], threshold: int
) -> dict[str, int]:
    results = checkpoint.get("results") or {}
    resolved = 0
    qualified = 0
    failed = 0
    unavailable = 0
    for video in source_videos:
        result = results.get(video["video_id"]) or {}
        count = result.get("comment_count")
        if isinstance(count, int):
            resolved += 1
            qualified += int(count > threshold)
        elif result.get("status") == "unavailable_null_count":
            unavailable += 1
        elif result.get("attempts"):
            failed += 1
    return {
        "source": len(source_videos),
        "completed": resolved + unavailable,
        "resolved": resolved,
        "qualified": qualified,
        "not_qualified": resolved - qualified,
        "unavailable": unavailable,
        "failed_or_pending": len(source_videos) - resolved - unavailable,
        "attempted_unresolved": failed,
    }


def save_checkpoint(
    path: Path,
    checkpoint: dict[str, Any],
    source_videos: list[dict[str, Any]],
    threshold: int,
    *,
    event: str,
    details: dict[str, Any] | None = None,
) -> None:
    checkpoint["updated_at"] = utc_now()
    checkpoint["threshold"] = threshold
    checkpoint["event"] = event
    checkpoint["details"] = details or {}
    checkpoint["stats"] = checkpoint_stats(checkpoint, source_videos, threshold)
    atomic_json(path, checkpoint)


def render_markdown(
    output: Path,
    source_videos: list[dict[str, Any]],
    checkpoint: dict[str, Any],
    threshold: int,
    keyword_order: dict[str, int],
) -> None:
    aggregate: dict[str, dict[str, Any]] = {}
    for video in source_videos:
        restored = dict(video)
        restored["keywords"] = set(restored.get("keywords") or [])
        aggregate[restored["video_id"]] = restored
    base_rows = {
        row["视频链接"]: row
        for row in build_markdown_records(aggregate, keyword_order)
    }
    results = checkpoint.get("results") or {}
    rows: list[dict[str, Any]] = []
    for video in source_videos:
        result = results.get(video["video_id"]) or {}
        count = result.get("comment_count")
        if not isinstance(count, int) or count <= threshold:
            continue
        row = dict(base_rows[video["video_url"]])
        row["评论数量"] = count
        row["链接来源"] = "搜索接口 + GraphQL commentListQuery"
        row["采集时间"] = _japan_time(result.get("queried_at"))
        rows.append(row)
    relevance_order = {"强相关": 0, "场景相关": 1, "弱相关": 2}
    rows.sort(
        key=lambda row: (
            -int(row["评论数量"]),
            relevance_order.get(str(row["相关度"]), 9),
            str(row["视频链接"]),
        )
    )
    stats = checkpoint_stats(checkpoint, source_videos, threshold)
    lines = [
        f"# 快手视频评论数量大于 {threshold}（最终结果）",
        "",
        f"- 数据源视频：{stats['source']} 条（已全局去重）",
        f"- 评论数已取得：{stats['resolved']} 条",
        f"- 评论数大于 {threshold}：{stats['qualified']} 条",
        f"- 评论数不大于 {threshold}：{stats['not_qualified']} 条",
        f"- 评论总数不可用：{stats['unavailable']} 条",
        f"- 未完成或查询失败：{stats['failed_or_pending']} 条",
        f"- 最后更新：{_japan_time(checkpoint.get('updated_at'))}",
        "- 评论内容未读取、未保存；仅请求评论总数。",
        "- 查询通过快手页面上下文执行，无需逐个打开视频详情页。",
        "",
        "| 相关度 | 内容分类 | 匹配依据 | 关键词 | 标题 | 作者 | 视频链接 | 评论数量 | 链接来源 | 采集时间 |",
        "|---|---|---|---|---|---|---|---:|---|---|",
    ]
    columns = [
        "相关度",
        "内容分类",
        "匹配依据",
        "关键词",
        "标题",
        "作者",
        "视频链接",
        "评论数量",
        "链接来源",
        "采集时间",
    ]
    for row in rows:
        lines.append(
            "| " + " | ".join(_clean_cell(row[column]) for column in columns) + " |"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(output)


class CommentClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def json(self, method: str, path: str, payload: dict | None = None) -> dict:
        response = self.client.request(method, path, json=payload)
        response.raise_for_status()
        return response.json()

    def ensure_kuaishou_page(self) -> None:
        status = self.json("GET", "/api/probe/status")
        if status.get("probe_state") in {"starting", "running", "error"}:
            try:
                self.json("POST", "/api/probe/stop", {})
            except httpx.HTTPStatusError:
                pass
        status = self.json("GET", "/api/probe/status")
        if status.get("browser_state") != "running" or not str(
            status.get("page_url") or ""
        ).startswith("https://www.kuaishou.com"):
            self.json(
                "POST",
                "/api/browser/navigate",
                {"url": "https://www.kuaishou.com/?isHome=1&source=SEARCH"},
            )

    def comment_count(self, video_id: str, timeout_seconds: float) -> dict:
        return self.json(
            "POST",
            "/api/probe/direct-comment",
            {"video_id": video_id, "timeout_seconds": timeout_seconds},
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("runtime/sampling/master/deduped_links.json"),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("runtime/comments/master-comment-counts.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "outputs/kuaishou-anonymous-sample-20260908/"
            "快手视频评论数大于50_最终结果.md"
        ),
    )
    parser.add_argument("--keywords", type=Path, default=Path("resources/keywords/vpn_kuaishou_search_keywords.tsv"))
    parser.add_argument("--threshold", type=int, default=50)
    parser.add_argument("--min-interval", type=float, default=1.5)
    parser.add_argument("--max-interval", type=float, default=3.5)
    parser.add_argument("--query-timeout", type=float, default=15.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    if args.threshold < 0:
        raise ValueError("threshold must be non-negative")
    if args.min_interval < 0 or args.min_interval > args.max_interval:
        raise ValueError("invalid query interval range")
    if args.max_attempts < 1:
        raise ValueError("max attempts must be positive")

    source_videos, source_updated_at = load_source(args.input)
    checkpoint = load_checkpoint(args.checkpoint, args.input)
    checkpoint["source_updated_at"] = source_updated_at
    checkpoint["source_video_count"] = len(source_videos)
    checkpoint["phase"] = "running"
    keyword_rows = load_keywords(args.keywords, 1_000_000)
    keyword_order = {
        row["keyword"]: index for index, row in enumerate(keyword_rows)
    }
    selected = source_videos[: args.limit] if args.limit > 0 else source_videos
    results: dict[str, dict[str, Any]] = checkpoint["results"]
    client = CommentClient(args.base_url, timeout=args.query_timeout + 10)
    paused_reason = None
    try:
        client.ensure_kuaishou_page()
        for pass_number in range(1, args.max_attempts + 1):
            pending = [
                video
                for video in selected
                if not isinstance(
                    (results.get(video["video_id"]) or {}).get("comment_count"),
                    int,
                )
                and (results.get(video["video_id"]) or {}).get("status")
                != "unavailable_null_count"
                and int((results.get(video["video_id"]) or {}).get("attempts") or 0)
                < args.max_attempts
            ]
            if not pending:
                break
            for position, video in enumerate(pending, start=1):
                delay = random.uniform(args.min_interval, args.max_interval)
                time.sleep(delay)
                video_id = video["video_id"]
                previous = dict(results.get(video_id) or {})
                attempts = int(previous.get("attempts") or 0) + 1
                queried_at = utc_now()
                try:
                    response = client.comment_count(video_id, args.query_timeout)
                    count = response.get("comment_count")
                    intercept = response.get("intercept_result")
                    if isinstance(count, int):
                        result = {
                            "video_id": video_id,
                            "comment_count": count,
                            "attempts": attempts,
                            "status": "resolved",
                            "queried_at": queried_at,
                            "http_status": response.get("http_status"),
                            "intercept_result": intercept,
                            "error": None,
                        }
                    elif (
                        response.get("transport_ok")
                        and response.get("http_status") == 200
                        and isinstance(
                            ((response.get("response") or {}).get("data") or {}).get(
                                "visionCommentList"
                            ),
                            dict,
                        )
                    ):
                        result = {
                            "video_id": video_id,
                            "comment_count": None,
                            "attempts": attempts,
                            "status": "unavailable_null_count",
                            "queried_at": queried_at,
                            "http_status": response.get("http_status"),
                            "intercept_result": intercept,
                            "error": "commentCount/commentCountV2 are null",
                        }
                    else:
                        result = {
                            "video_id": video_id,
                            "comment_count": None,
                            "attempts": attempts,
                            "status": "risk_control"
                            if intercept and "risk-control" in str(intercept)
                            else "query_failed",
                            "queried_at": queried_at,
                            "http_status": response.get("http_status"),
                            "intercept_result": intercept,
                            "error": response.get("error")
                            or "comment count missing from response",
                        }
                except Exception as exc:
                    result = {
                        "video_id": video_id,
                        "comment_count": None,
                        "attempts": attempts,
                        "status": "query_failed",
                        "queried_at": queried_at,
                        "http_status": None,
                        "intercept_result": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                results[video_id] = result
                save_checkpoint(
                    args.checkpoint,
                    checkpoint,
                    source_videos,
                    args.threshold,
                    event="comment_resolved"
                    if isinstance(result.get("comment_count"), int)
                    else "comment_query_failed",
                    details={
                        "video_id": video_id,
                        "pass": pass_number,
                        "position": position,
                        "pending_in_pass": len(pending),
                        "delay_seconds": round(delay, 3),
                        "status": result["status"],
                    },
                )
                stats = checkpoint["stats"]
                print(
                    f"comments resolved={stats['resolved']}/{len(source_videos)} "
                    f"qualified>{args.threshold}={stats['qualified']} "
                    f"video={video_id} status={result['status']}",
                    flush=True,
                )
                if stats["resolved"] % 10 == 0 or result["status"] != "resolved":
                    render_markdown(
                        args.output,
                        source_videos,
                        checkpoint,
                        args.threshold,
                        keyword_order,
                    )
                if result["status"] == "risk_control":
                    paused_reason = f"risk-control at {video_id}"
                    break
            if paused_reason:
                break
    except KeyboardInterrupt:
        paused_reason = "user_paused"
    finally:
        client.close()

    stats = checkpoint_stats(checkpoint, source_videos, args.threshold)
    if paused_reason:
        checkpoint["phase"] = "paused"
        event = "paused"
    elif stats["resolved"] + stats["unavailable"] == len(source_videos):
        checkpoint["phase"] = "completed"
        event = "completed"
    else:
        checkpoint["phase"] = "completed_with_errors"
        event = "completed_with_errors"
    save_checkpoint(
        args.checkpoint,
        checkpoint,
        source_videos,
        args.threshold,
        event=event,
        details={"reason": paused_reason} if paused_reason else None,
    )
    render_markdown(
        args.output,
        source_videos,
        checkpoint,
        args.threshold,
        keyword_order,
    )
    print(
        json.dumps(
            {
                "phase": checkpoint["phase"],
                "output": str(args.output),
                **checkpoint["stats"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
