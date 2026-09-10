import json

import pytest
from pydantic import ValidationError

from backend.kuaishou.sampling_task import SamplingTaskManager
from backend.models import SamplingStartRequest


def test_sampling_status_restores_latest_paused_run_and_master_count(tmp_path):
    older = tmp_path / "sampling" / "20260908-100000"
    current = tmp_path / "sampling" / "20260908-110000"
    master = tmp_path / "sampling" / "master"
    for directory in (older, current, master):
        directory.mkdir(parents=True)
    (older / "progress.json").write_text(
        json.dumps({"phase": "completed", "current_keyword": "old"})
    )
    (current / "progress.json").write_text(
        json.dumps({"phase": "paused", "current_keyword": "vpn"})
    )
    (current / "aggregate.json").write_text(
        json.dumps({"metrics": [{"keyword": "vpn"}], "videos": [{"video_id": "1"}]})
    )
    (master / "deduped_links.json").write_text(json.dumps({"video_count": 570}))

    manager = SamplingTaskManager(tmp_path, project_root=tmp_path)
    status = manager.status()

    assert status["active_run_dir"] == str(current)
    assert status["progress"]["current_keyword"] == "vpn"
    assert status["run_unique_links"] == 1
    assert status["master_unique_links"] == 570


def test_sampling_request_defaults_and_interval_validation():
    request = SamplingStartRequest()
    assert request.min_interval == 6
    assert request.max_interval == 12
    assert request.max_consecutive == 5
    assert request.open_browser_window is True
    assert request.auto_reset_identity is False
    with pytest.raises(ValidationError):
        SamplingStartRequest(min_interval=13, max_interval=12)


def test_manual_identity_reset_acknowledges_failed_keyword(tmp_path):
    run = tmp_path / "sampling" / "20260908-120000"
    run.mkdir(parents=True)
    (run / "progress.json").write_text(
        json.dumps(
            {
                "phase": "paused",
                "event": "manual_reset_required",
                "current_keyword": "vpn",
                "current_keyword_index": "6",
                "total_identity_resets": 2,
            }
        )
    )
    (run / "6-vpn.json").write_text(
        json.dumps(
            {
                "keyword": "vpn",
                "last_event": "manual_reset_required",
                "identity_resets": 2,
            }
        )
    )
    (run / "aggregate.json").write_text(
        json.dumps(
            {
                "metrics": [{"keyword": "vpn", "identity_resets": 2}],
                "videos": [],
            }
        )
    )
    manager = SamplingTaskManager(tmp_path, project_root=tmp_path)

    manager.acknowledge_identity_reset()

    progress = json.loads((run / "progress.json").read_text())
    checkpoint = json.loads((run / "6-vpn.json").read_text())
    aggregate = json.loads((run / "aggregate.json").read_text())
    assert progress["event"] == "identity_reset"
    assert progress["total_identity_resets"] == 3
    assert checkpoint["last_event"] == "identity_reset"
    assert checkpoint["identity_resets"] == 3
    assert aggregate["metrics"][0]["identity_resets"] == 3


def test_comment_status_is_compact_and_exposes_recent_errors(tmp_path):
    comment_dir = tmp_path / "comments"
    comment_dir.mkdir()
    (comment_dir / "master-comment-counts.json").write_text(
        json.dumps(
            {
                "phase": "running",
                "event": "comment_query_failed",
                "updated_at": "2026-09-08T00:00:00+00:00",
                "stats": {
                    "source": 738,
                    "resolved": 10,
                    "qualified": 8,
                    "not_qualified": 2,
                    "failed_or_pending": 728,
                    "attempted_unresolved": 1,
                },
                "results": {
                    "OK": {"comment_count": 100, "attempts": 1},
                    "BAD": {
                        "comment_count": None,
                        "attempts": 2,
                        "status": "query_failed",
                        "error": "timeout",
                        "queried_at": "2026-09-08T00:00:00+00:00",
                    },
                },
            }
        )
    )
    manager = SamplingTaskManager(tmp_path, project_root=tmp_path)
    status = manager.status()["comment_progress"]
    assert status["stats"]["resolved"] == 10
    assert status["recent_errors"][0]["video_id"] == "BAD"
    assert "results" not in status


def test_task_status_counts_full_keyword_library(tmp_path):
    path = tmp_path / "resources" / "keywords"
    path.mkdir(parents=True)
    (path / "vpn_kuaishou_search_keywords.tsv").write_text(
        "keyword_index\tkeyword\n1\tvpn\n2\topenvpn\n3\t加速器\n",
        encoding="utf-8",
    )
    manager = SamplingTaskManager(tmp_path / "runtime", project_root=tmp_path)
    assert manager.status()["keyword_library_count"] == 3


def test_controller_restores_detailed_process_error(tmp_path):
    sampling = tmp_path / "sampling"
    run = sampling / "run-a"
    run.mkdir(parents=True)
    (run / "progress.json").write_text(
        json.dumps(
            {
                "phase": "interrupted",
                "details": {"error": "HTTPStatusError: login-status returned 500"},
            }
        )
    )
    (sampling / "controller.json").write_text(
        json.dumps(
            {
                "state": "error",
                "active_run_dir": str(run),
                "returncode": 1,
                "last_error": "采集进程退出码：1",
            }
        )
    )

    manager = SamplingTaskManager(tmp_path, project_root=tmp_path)

    assert manager.status()["last_error"] == (
        "采集进程异常：HTTPStatusError: login-status returned 500"
    )
