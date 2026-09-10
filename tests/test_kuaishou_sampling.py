import json
from pathlib import Path

import httpx
import pytest

from scripts.sample_kuaishou_top_keywords import (
    ManualResetRequired,
    Sampler,
    _needs_identity_reset,
    classify_video,
    keyword_checkpoint_path,
    load_master_record,
    load_keywords,
    merge_into_master,
    merge_video,
    render_master_markdown,
    render_markdown,
    save_master_record,
    save_runtime_snapshot,
)


def test_classify_video_matches_existing_workbook_categories():
    assert classify_video("VPN和翻墙有什么区别", ["vpn"])[0:2] == (
        "强相关",
        "VPN/翻墙",
    )
    assert classify_video("四款免费游戏加速器", ["游戏加速器"])[0:2] == (
        "强相关",
        "加速器",
    )
    assert classify_video("海外网络连接失败", ["海外网络"])[0:2] == (
        "场景相关",
        "海外访问/跨境网络",
    )


def test_merge_and_markdown_dedupe_video_id(tmp_path):
    aggregate = {}
    order = {"vpn": 0, "vpn下载": 1}
    video = {
        "video_id": "VIDEO1",
        "video_url": "https://www.kuaishou.com/short-video/VIDEO1",
        "title": "VPN | 测试\n标题",
        "author_name": "作者",
        "first_seen_at": "2026-09-08T00:00:00+00:00",
    }
    merge_video(aggregate, video, "vpn", order)
    merge_video(aggregate, video, "vpn下载", order)
    assert len(aggregate) == 1
    assert aggregate["VIDEO1"]["keywords"] == {"vpn", "vpn下载"}

    output = tmp_path / "result.md"
    keyword_rows = [{"keyword": "vpn"}, {"keyword": "vpn下载"}]
    metrics = [
        {
            "keyword": "vpn",
            "successful_rounds": 2,
            "failed_rounds": 0,
            "raw_feed_rows": 40,
            "unique_videos": 30,
        },
        {
            "keyword": "vpn下载",
            "successful_rounds": 2,
            "failed_rounds": 0,
            "raw_feed_rows": 40,
            "unique_videos": 25,
        },
    ]
    render_markdown(
        output,
        keyword_rows,
        metrics,
        aggregate,
        "2026-09-08T00:00:00+00:00",
        "2026-09-08T01:00:00+00:00",
        2,
    )
    text = output.read_text(encoding="utf-8")
    assert "全局按视频ID/链接去重后：1" in text
    assert "vpn、vpn下载" in text
    assert "VPN \\| 测试 标题" in text


def test_load_keywords_respects_limit(tmp_path):
    path = tmp_path / "keywords.tsv"
    path.write_text(
        "keyword\tsearch_url\n一\thttps://www.kuaishou.com/search/1\n二\thttps://www.kuaishou.com/search/2\n",
        encoding="utf-8",
    )
    assert [row["keyword"] for row in load_keywords(path, 1)] == ["一"]


def test_runtime_snapshot_records_deduped_links_and_resume_position(tmp_path):
    aggregate = {
        "VIDEO1": {
            "video_id": "VIDEO1",
            "video_url": "https://www.kuaishou.com/short-video/VIDEO1",
            "title": "标题",
            "author_name": "作者",
            "keywords": {"vpn"},
            "first_seen_at": "2026-09-08T00:00:00+00:00",
        }
    }
    metric = {
        "keyword": "vpn",
        "attempted_rounds": 7,
        "successful_rounds": 4,
        "failed_rounds": 3,
        "raw_feed_rows": 80,
        "unique_videos": 1,
        "identity_resets": 2,
    }
    keywords = [
        {"keyword_index": "1", "keyword": "虚拟专用网络"},
        {"keyword_index": "6", "keyword": "vpn"},
    ]

    save_runtime_snapshot(
        tmp_path,
        started_at="2026-09-08T00:00:00+00:00",
        keyword_rows=keywords,
        keyword_position=2,
        metric=metric,
        metrics=[metric],
        aggregate=aggregate,
        requested_loops=20,
        event="network_error_checkpointed",
        details={"error": "search_response_failed"},
    )

    progress = json.loads((tmp_path / "progress.json").read_text())
    links = json.loads((tmp_path / "deduped_links.json").read_text())
    assert progress["current_keyword_position"] == 2
    assert progress["current_keyword"] == "vpn"
    assert progress["resume_from_round"] == 8
    assert progress["global_unique_links"] == 1
    assert progress["schedule"] == {
        "min_interval_seconds": 6.0,
        "max_interval_seconds": 12.0,
        "max_consecutive_per_keyword": 5,
        "auto_reset_identity": False,
    }
    assert links["count"] == 1
    assert links["videos"][0]["keywords"] == ["vpn"]


def test_failed_checkpoint_is_saved_before_deep_reset_and_resume(tmp_path, monkeypatch):
    checkpoint = tmp_path / "6-vpn.json"
    checkpoint.write_text(
        json.dumps(
            {
                "keyword": "vpn",
                "runs": [{"attempt": 1, "successful": False}],
                "successful_rounds": 0,
                "failed_rounds": 1,
                "raw_feed_rows": 0,
                "videos": [],
            }
        )
    )
    timeline = []

    class FakeSampler(Sampler):
        def __init__(self):
            self.active = False

        def _json(self, method, path, payload=None):
            if method == "POST" and path == "/api/probe/start":
                self.active = True
                return {
                    "page_url": "https://www.kuaishou.com/search/vpn",
                    "search_navigation": {"verified": True},
                }
            if method == "POST" and path == "/api/probe/stop":
                self.active = False
            if method == "GET" and path == "/api/probe/status":
                return {"probe_state": "running" if self.active else "stopped"}
            return {}

        def wait_first_response(self, timeout_seconds=25.0):
            return {
                "probe": {
                    "risk_controls": [],
                    "extracted": {
                        "successful_search_responses": 1,
                        "failed_search_responses": 0,
                        "search_feed_rows": 20,
                        "videos": [
                            {
                                "video_id": "VIDEO1",
                                "video_url": "https://www.kuaishou.com/short-video/VIDEO1",
                                "title": "VPN测试",
                                "author_name": "作者",
                            }
                        ],
                    },
                }
            }

        def reset_anonymous_identity(self):
            timeline.append("deep_reset")
            return {"mode": "deep", "anonymous_verified": True}

    monkeypatch.setattr("scripts.sample_kuaishou_top_keywords.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "scripts.sample_kuaishou_top_keywords.random.uniform", lambda _a, _b: 2.5
    )
    sampler = FakeSampler()
    metric, videos = sampler.sample_keyword(
        "vpn",
        2,
        0.5,
        5.0,
        checkpoint,
        20,
        2,
        True,
        lambda _metric, _videos, event, _details: timeline.append(event),
    )

    assert timeline.index("network_error_checkpointed") < timeline.index("deep_reset")
    assert timeline.index("deep_reset") < timeline.index("identity_reset")
    assert metric["successful_rounds"] == 1
    assert metric["identity_resets"] == 1
    assert videos[0]["video_id"] == "VIDEO1"
    saved = json.loads(checkpoint.read_text())
    assert saved["runs"][0]["identity_reset_handled"] is True


def test_master_record_merges_runs_and_updates_one_markdown(tmp_path):
    master = {}
    order = {"vpn": 0, "vpn下载": 1}
    merge_into_master(
        master,
        [
            {
                "video_id": "VIDEO1",
                "video_url": "https://www.kuaishou.com/short-video/VIDEO1",
                "title": "VPN说明",
                "author_name": "作者一",
                "keywords": ["vpn"],
                "first_seen_at": "2026-09-08T01:00:00+00:00",
            }
        ],
        order,
    )
    merge_into_master(
        master,
        [
            {
                "video_id": "VIDEO1",
                "video_url": "https://www.kuaishou.com/short-video/VIDEO1",
                "title": "VPN说明",
                "author_name": "作者一",
                "keywords": ["vpn下载"],
                "first_seen_at": "2026-09-08T02:00:00+00:00",
            },
            {
                "video_id": "VIDEO2",
                "video_url": "https://www.kuaishou.com/short-video/VIDEO2",
                "title": "网络说明",
                "author_name": "作者二",
                "keywords": ["vpn下载"],
            },
        ],
        order,
    )
    assert len(master) == 2
    assert master["VIDEO1"]["keywords"] == {"vpn", "vpn下载"}

    master_json = tmp_path / "master.json"
    master_md = tmp_path / "master.md"
    sources = {
        "run-a": {"run_id": "run-a", "unique_video_count": 1, "status": "recorded"},
        "run-b": {"run_id": "run-b", "unique_video_count": 2, "status": "active"},
    }
    save_master_record(master_json, master, sources)
    loaded, loaded_sources = load_master_record(master_json)
    render_master_markdown(master_md, loaded, loaded_sources, order)

    assert len(loaded) == 2
    text = master_md.read_text(encoding="utf-8")
    assert "全局按视频ID/链接去重：2 条" in text
    assert "vpn、vpn下载" in text
    assert "run-a" in text and "run-b" in text


def test_auto_reset_off_stops_for_manual_reset(tmp_path, monkeypatch):
    events = []

    class FailedSearchSampler(Sampler):
        def __init__(self):
            self.active = False

        def _json(self, method, path, payload=None):
            if method == "POST" and path == "/api/probe/start":
                self.active = True
                return {
                    "page_url": "https://www.kuaishou.com/search/vpn",
                    "search_navigation": {"verified": True},
                }
            if method == "POST" and path == "/api/probe/stop":
                self.active = False
            if method == "GET" and path == "/api/probe/status":
                return {"probe_state": "running" if self.active else "stopped"}
            return {}

        def wait_first_response(self, timeout_seconds=25.0):
            return {
                "probe": {
                    "risk_controls": [{"intercept_result": "risk-control;2"}],
                    "extracted": {
                        "successful_search_responses": 0,
                        "failed_search_responses": 1,
                        "search_feed_rows": 0,
                        "videos": [],
                    },
                }
            }

        def reset_anonymous_identity(self):
            raise AssertionError("automatic reset must stay disabled")

    monkeypatch.setattr("scripts.sample_kuaishou_top_keywords.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "scripts.sample_kuaishou_top_keywords.random.uniform", lambda _a, _b: 8
    )
    sampler = FailedSearchSampler()
    with pytest.raises(ManualResetRequired):
        sampler.sample_keyword(
            "vpn",
            20,
            6,
            12,
            tmp_path / "vpn.json",
            250,
            3,
            False,
            lambda _metric, _videos, event, _details: events.append(event),
        )
    assert "manual_reset_required" in events


def test_pending_reset_ignores_navigation_failures_and_handled_search_failures():
    assert _needs_identity_reset(
        {
            "last_event": "network_error_checkpointed",
            "runs": [{"successful": False, "counted_as_round": True}],
        }
    )
    assert not _needs_identity_reset(
        {
            "last_event": "navigation_retry_without_reset",
            "runs": [{"successful": False, "counted_as_round": False}],
        }
    )
    assert not _needs_identity_reset(
        {
            "last_event": "batch_complete",
            "runs": [
                {
                    "successful": False,
                    "counted_as_round": True,
                    "identity_reset_handled": True,
                }
            ],
        }
    )


def test_checkpoint_filename_is_safe_for_full_keyword_library(tmp_path):
    path = keyword_checkpoint_path(
        tmp_path, {"keyword_index": "11", "keyword": "vpn/教程?节点"}
    )
    assert path.parent == tmp_path
    assert "/" not in path.name
    assert "?" not in path.name
    assert path.suffix == ".json"


def test_anonymous_browser_recovers_stale_login_page_without_identity_reset():
    calls = []
    login_attempts = 0

    class RecoveringSampler(Sampler):
        def __init__(self):
            pass

        def _json(self, method, path, payload=None):
            nonlocal login_attempts
            calls.append((method, path))
            if method == "GET" and path == "/api/probe/status":
                return {
                    "probe_state": "stopped",
                    "browser_state": "running",
                    "page_url": "https://www.kuaishou.com/?isHome=1&source=SEARCH",
                }
            if method == "GET" and path == "/api/browser/login-status":
                login_attempts += 1
                if login_attempts == 1:
                    request = httpx.Request("GET", "http://test/api/browser/login-status")
                    response = httpx.Response(500, request=request)
                    raise httpx.HTTPStatusError(
                        "stale page", request=request, response=response
                    )
                return {"logged_in": False, "check_login": False}
            if method == "POST" and path in {
                "/api/browser/close",
                "/api/browser/navigate",
            }:
                return {"browser_state": "running"}
            raise AssertionError((method, path, payload))

    sampler = RecoveringSampler()
    login = sampler.ensure_anonymous_browser()

    assert login["logged_in"] is False
    assert login_attempts == 2
    assert ("POST", "/api/browser/close") in calls
    assert ("POST", "/api/browser/navigate") in calls
