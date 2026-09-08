from __future__ import annotations

import json
from pathlib import Path

from backend.kuaishou.response_parser import (
    KuaishouResponseParser,
    omit_comment_content,
    parse_count,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_count_units():
    assert parse_count(1220) == 1220
    assert parse_count("1.2万") == 12000
    assert parse_count("2.5k") == 2500
    assert parse_count(None) is None
    assert parse_count("unknown") is None


def test_search_and_comment_count_responses_are_merged():
    parser = KuaishouResponseParser("vpn")
    events = parser.consume(
        url="https://www.kuaishou.com/rest/v/search/feed?caver=2",
        operation_names=[],
        request_data={"keyword": "vpn", "pcursor": ""},
        response_data=load_fixture("kuaishou_search_feed.json"),
    )
    assert len([event for event in events if event["data_kind"] == "search_video"]) == 2
    assert parser.status()["video_count"] == 2
    assert parser.status()["comment_count_resolved"] == 0

    events = parser.consume(
        url="https://www.kuaishou.com/graphql",
        operation_names=["commentListQuery"],
        request_data={"variables": {"photoId": "3xrkgjnv59hha7u", "pcursor": ""}},
        response_data=load_fixture("kuaishou_comment_count.json"),
    )
    assert events == [
        {
            "data_kind": "comment_count",
            "video_id": "3xrkgjnv59hha7u",
            "comment_count": 1220,
            "qualifies_default": True,
        }
    ]
    status = parser.status()
    assert status["comment_count_resolved"] == 1
    assert status["qualifying_over_50"] == 1
    assert status["videos"][0]["author_name"] == "科技宅阿梨"
    assert status["videos"][0]["video_url"].endswith("/3xrkgjnv59hha7u")


def test_comment_pagination_is_ignored_and_content_is_omitted():
    fixture = load_fixture("kuaishou_comment_count.json")
    parser = KuaishouResponseParser("vpn")
    events = parser.consume(
        url="https://www.kuaishou.com/graphql",
        operation_names=["commentListQuery"],
        request_data={"variables": {"photoId": "VIDEO", "pcursor": "NEXT"}},
        response_data=fixture,
    )
    assert events == []

    sanitized = omit_comment_content(fixture)
    comment_feed = sanitized["data"]["visionCommentList"]
    assert comment_feed["commentCountV2"] == 1220
    assert comment_feed["rootCommentsV2"] == {"omitted": True, "row_count": 1}
    assert "COMMENT_CONTENT" not in json.dumps(sanitized)

