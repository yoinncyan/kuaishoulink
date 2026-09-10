"""Parsers for Kuaishou responses observed inside the real browser session."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    text = str(value).strip().lower().replace(",", "")
    if not text:
        return None
    multiplier = 1
    for suffix, factor in (("万", 10_000), ("w", 10_000), ("千", 1_000), ("k", 1_000)):
        if text.endswith(suffix):
            multiplier = factor
            text = text[: -len(suffix)]
            break
    try:
        number = float(text)
    except ValueError:
        return None
    return max(0, int(number * multiplier))


@dataclass
class VideoRecord:
    video_id: str
    video_url: str
    title: str = ""
    author_name: str = ""
    author_id: str | None = None
    comment_count: int | None = None
    matched_keywords: set[str] = field(default_factory=set)
    source: str = "search_feed"
    first_seen_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["matched_keywords"] = sorted(self.matched_keywords)
        return payload


class KuaishouResponseParser:
    """Stateful merger for search results and comment-count responses."""

    def __init__(self, keyword: str):
        self.keyword = keyword
        self.videos: dict[str, VideoRecord] = {}
        self.search_cursor: str | None = None
        self.successful_search_responses = 0
        self.failed_search_responses = 0
        self.search_feed_rows = 0
        self.comment_responses = 0

    def consume(
        self,
        *,
        url: str,
        operation_names: list[str],
        request_data: Any,
        response_data: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(response_data, dict):
            return []
        path = urlsplit(url).path
        if path == "/rest/v/search/feed":
            return self._consume_search_feed(response_data)
        if "commentListQuery" in operation_names:
            return self._consume_comment_count(request_data, response_data)
        return []

    def _consume_search_feed(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if payload.get("result") != 1 or not isinstance(payload.get("feeds"), list):
            self.failed_search_responses += 1
            return []
        self.successful_search_responses += 1
        self.search_feed_rows += len(payload["feeds"])
        cursor = payload.get("pcursor")
        self.search_cursor = str(cursor) if cursor is not None else None
        events: list[dict[str, Any]] = []
        for feed in payload["feeds"]:
            if not isinstance(feed, dict):
                continue
            photo = feed.get("photo")
            author = feed.get("author")
            if not isinstance(photo, dict):
                continue
            video_id = str(photo.get("id") or "").strip()
            if not video_id:
                continue
            author = author if isinstance(author, dict) else {}
            existing = self.videos.get(video_id)
            if existing is None:
                existing = VideoRecord(
                    video_id=video_id,
                    video_url=f"https://www.kuaishou.com/short-video/{video_id}",
                    title=str(photo.get("caption") or "").strip(),
                    author_name=str(author.get("name") or "").strip(),
                    author_id=str(author.get("id")) if author.get("id") else None,
                    comment_count=parse_count(photo.get("commentCount")),
                    matched_keywords={self.keyword},
                )
                self.videos[video_id] = existing
                events.append(
                    {
                        "data_kind": "search_video",
                        "video_id": video_id,
                        "title": existing.title,
                        "author_name": existing.author_name,
                        "comment_count": existing.comment_count,
                    }
                )
            else:
                existing.matched_keywords.add(self.keyword)
                existing.updated_at = _now()
                if not existing.title:
                    existing.title = str(photo.get("caption") or "").strip()
                if not existing.author_name:
                    existing.author_name = str(author.get("name") or "").strip()
                if existing.author_id is None and author.get("id"):
                    existing.author_id = str(author["id"])
        events.append(
            {
                "data_kind": "search_page",
                "keyword": self.keyword,
                "received": len(payload["feeds"]),
                "unique_videos": len(self.videos),
                "pcursor": self.search_cursor,
            }
        )
        return events

    def _consume_comment_count(
        self, request_data: Any, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        self.comment_responses += 1
        variables = request_data.get("variables", {}) if isinstance(request_data, dict) else {}
        video_id = str(variables.get("photoId") or "").strip()
        # Pagination responses repeat the total.  The first request is enough
        # and deliberately avoids consuming or persisting comment content.
        cursor = variables.get("pcursor")
        if not video_id or cursor not in (None, ""):
            return []
        feed = (payload.get("data") or {}).get("visionCommentList")
        if not isinstance(feed, dict):
            return []
        count = parse_count(feed.get("commentCountV2"))
        if count is None:
            count = parse_count(feed.get("commentCount"))
        if count is None:
            return []
        record = self.videos.get(video_id)
        if record is None:
            record = VideoRecord(
                video_id=video_id,
                video_url=f"https://www.kuaishou.com/short-video/{video_id}",
                comment_count=count,
                matched_keywords={self.keyword},
                source="comment_query",
            )
            self.videos[video_id] = record
        else:
            record.comment_count = count
            record.updated_at = _now()
        return [
            {
                "data_kind": "comment_count",
                "video_id": video_id,
                "comment_count": count,
                "qualifies_default": count > 50,
            }
        ]

    def status(self) -> dict[str, Any]:
        records = [record.to_dict() for record in self.videos.values()]
        resolved = sum(record["comment_count"] is not None for record in records)
        qualifies = sum(
            (record["comment_count"] or 0) > 50
            for record in records
            if record["comment_count"] is not None
        )
        return {
            "keyword": self.keyword,
            "search_cursor": self.search_cursor,
            "successful_search_responses": self.successful_search_responses,
            "failed_search_responses": self.failed_search_responses,
            "search_feed_rows": self.search_feed_rows,
            "comment_responses": self.comment_responses,
            "video_count": len(records),
            "comment_count_resolved": resolved,
            "qualifying_over_50": qualifies,
            "videos": records,
        }


def omit_comment_content(payload: Any) -> Any:
    """Remove comment rows while retaining counts/cursors in saved fixtures."""
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    feed = data.get("visionCommentList") if isinstance(data, dict) else None
    if not isinstance(feed, dict):
        return payload
    copied = {**payload, "data": {**data}}
    sanitized_feed = {**feed}
    for key in ("rootComments", "rootCommentsV2"):
        rows = sanitized_feed.get(key)
        if isinstance(rows, list):
            sanitized_feed[key] = {"omitted": True, "row_count": len(rows)}
    copied["data"]["visionCommentList"] = sanitized_feed
    return copied
