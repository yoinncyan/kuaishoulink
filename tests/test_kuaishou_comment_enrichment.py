import json

import pytest

from scripts.enrich_kuaishou_comment_counts import (
    checkpoint_stats,
    load_source,
    render_markdown,
)


def videos():
    return [
        {
            "video_id": "VIDEO1",
            "video_url": "https://www.kuaishou.com/short-video/VIDEO1",
            "title": "VPN说明",
            "author_name": "作者一",
            "keywords": ["vpn"],
            "first_seen_at": "2026-09-08T00:00:00+00:00",
        },
        {
            "video_id": "VIDEO2",
            "video_url": "https://www.kuaishou.com/short-video/VIDEO2",
            "title": "网络说明",
            "author_name": "作者二",
            "keywords": ["vpn"],
            "first_seen_at": "2026-09-08T00:00:00+00:00",
        },
    ]


def test_final_markdown_uses_strict_greater_than_threshold(tmp_path):
    source = videos()
    checkpoint = {
        "updated_at": "2026-09-08T01:00:00+00:00",
        "results": {
            "VIDEO1": {
                "comment_count": 51,
                "queried_at": "2026-09-08T01:00:00+00:00",
            },
            "VIDEO2": {
                "comment_count": 50,
                "queried_at": "2026-09-08T01:00:00+00:00",
            },
        },
    }
    output = tmp_path / "final.md"
    render_markdown(output, source, checkpoint, 50, {"vpn": 0})

    text = output.read_text(encoding="utf-8")
    assert "评论数大于 50：1 条" in text
    assert "VIDEO1" in text
    assert "VIDEO2" not in text
    assert checkpoint_stats(checkpoint, source, 50)["not_qualified"] == 1


def test_master_source_rejects_duplicate_video_links(tmp_path):
    source = videos()
    source[1]["video_url"] = source[0]["video_url"]
    path = tmp_path / "master.json"
    path.write_text(json.dumps({"video_count": 2, "videos": source}))
    with pytest.raises(ValueError, match="duplicate video URLs"):
        load_source(path)
