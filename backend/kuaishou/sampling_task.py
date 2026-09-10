"""Background process controller for the resumable Kuaishou sampler."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SamplingTaskManager:
    """Start, pause and inspect one sampler subprocess.

    The sampler intentionally remains a separate process because it talks to
    the same FastAPI control plane that the Web UI uses.  SIGINT invokes its
    graceful checkpoint path, so the next Start operation resumes exactly from
    the per-keyword counters saved on disk.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        project_root: Path | None = None,
        base_url: str | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self.base_url = base_url or os.environ.get(
            "KUAISHOU_INTERNAL_BASE_URL", "http://127.0.0.1:8080"
        )
        self.sampling_root = self.data_dir / "sampling"
        self.controller_path = self.sampling_root / "controller.json"
        self.log_path = self.sampling_root / "controller.log"
        self.master_path = self.sampling_root / "master" / "deduped_links.json"
        self.keyword_file = (
            self.project_root
            / "resources"
            / "keywords"
            / "vpn_kuaishou_search_keywords.tsv"
        )
        self.comment_checkpoint_path = (
            self.data_dir / "comments" / "master-comment-counts.json"
        )
        self.comment_output_path = (
            self.project_root
            / "outputs"
            / "kuaishou-anonymous-sample-20260908"
            / "快手视频评论数大于50_最终结果.md"
        )
        self._process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._log_handle: Any | None = None
        self._lock = asyncio.Lock()
        self._state = "stopped"
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._last_error: str | None = None
        self._returncode: int | None = None
        self._pause_requested = False
        self._active_run_dir: Path | None = None
        self._config: dict[str, Any] = {}
        self._restore_controller()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @staticmethod
    def _process_error_message(
        progress: dict[str, Any] | None, returncode: int | None
    ) -> str:
        details = (progress or {}).get("details") or {}
        error = str(details.get("error") or "").strip()
        if error:
            return f"采集进程异常：{error}"
        return f"采集进程退出码：{returncode}"

    def _restore_controller(self) -> None:
        try:
            payload = json.loads(self.controller_path.read_text(encoding="utf-8"))
            run_dir = payload.get("active_run_dir")
            if run_dir:
                self._active_run_dir = Path(str(run_dir))
            self._config = dict(payload.get("config") or {})
            previous = str(payload.get("state") or "stopped")
            self._state = "paused" if previous in {"running", "pausing"} else previous
            self._started_at = payload.get("started_at")
            self._ended_at = payload.get("ended_at")
            self._last_error = payload.get("last_error")
            self._returncode = payload.get("returncode")
            if previous == "error" and self._active_run_dir is not None:
                progress = self._read_json(self._active_run_dir / "progress.json")
                self._last_error = self._process_error_message(
                    progress, self._returncode
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._active_run_dir = self._discover_resumable_run()
            if self._active_run_dir is not None:
                self._state = "paused"

    def _discover_resumable_run(self) -> Path | None:
        if not self.sampling_root.exists():
            return None
        candidates: list[tuple[float, Path]] = []
        for progress_path in self.sampling_root.glob("*/progress.json"):
            try:
                payload = json.loads(progress_path.read_text(encoding="utf-8"))
                if payload.get("phase") != "completed":
                    candidates.append((progress_path.stat().st_mtime, progress_path.parent))
            except (OSError, json.JSONDecodeError):
                continue
        return max(candidates)[1] if candidates else None

    def _new_run_dir(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = self.sampling_root / stamp
        suffix = 1
        while candidate.exists():
            candidate = self.sampling_root / f"{stamp}-{suffix}"
            suffix += 1
        return candidate

    def _write_controller(self) -> None:
        self.sampling_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "state": self._state,
            "pid": self._process.pid if self.running else None,
            "active_run_dir": str(self._active_run_dir)
            if self._active_run_dir
            else None,
            "config": self._config,
            "started_at": self._started_at,
            "ended_at": self._ended_at,
            "last_error": self._last_error,
            "returncode": self._returncode,
            "updated_at": utc_now(),
        }
        temporary = self.controller_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.controller_path)

    async def start(self, config: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if self.running:
                raise RuntimeError("采集任务已经在运行")
            run_dir = self._active_run_dir or self._discover_resumable_run()
            if run_dir is None:
                run_dir = self._new_run_dir()
            run_dir.mkdir(parents=True, exist_ok=True)
            self._active_run_dir = run_dir
            self._config = dict(config)
            self._state = "starting"
            self._started_at = utc_now()
            self._ended_at = None
            self._last_error = None
            self._returncode = None
            self._pause_requested = False
            self._write_controller()

            output_root = (
                self.project_root
                / "outputs"
                / "kuaishou-anonymous-sample-20260908"
            )
            if config.get("stage") == "comments":
                script = (
                    self.project_root
                    / "scripts"
                    / "enrich_kuaishou_comment_counts.py"
                )
                command = [
                    sys.executable,
                    str(script),
                    "--input",
                    str(self.master_path),
                    "--checkpoint",
                    str(self.comment_checkpoint_path),
                    "--output",
                    str(self.comment_output_path),
                    "--threshold",
                    "50",
                    "--min-interval",
                    str(config["comment_min_interval"]),
                    "--max-interval",
                    str(config["comment_max_interval"]),
                    "--query-timeout",
                    "15",
                    "--max-attempts",
                    "3",
                    "--base-url",
                    self.base_url,
                ]
            else:
                script = (
                    self.project_root
                    / "scripts"
                    / "sample_kuaishou_top_keywords.py"
                )
                command = [
                    sys.executable,
                    str(script),
                    "--limit",
                    str(config["limit"]),
                    "--loops",
                    str(config["loops"]),
                    "--min-interval",
                    str(config["min_interval"]),
                    "--max-interval",
                    str(config["max_interval"]),
                    "--max-consecutive",
                    str(config["max_consecutive"]),
                    "--max-attempts",
                    str(config["max_attempts"]),
                    "--resume-dir",
                    str(run_dir),
                    "--base-url",
                    self.base_url,
                    "--output",
                    str(output_root / "全部关键词_每词20次_累计去重结果.md"),
                ]
                if config.get("open_browser_window"):
                    command.append("--allow-visible-browser")
                if config.get("auto_reset_identity"):
                    command.append("--auto-reset-identity")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log_path.open("a", encoding="utf-8")
            self._log_handle.write(
                f"\n[{utc_now()}] START {' '.join(command)}\n"
            )
            self._log_handle.flush()
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(self.project_root),
                    stdout=self._log_handle,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except Exception:
                self._state = "error"
                self._ended_at = utc_now()
                if self._log_handle is not None:
                    self._log_handle.close()
                    self._log_handle = None
                self._write_controller()
                raise
            self._state = "running"
            self._write_controller()
            self._monitor_task = asyncio.create_task(self._monitor())
        return self.status()

    async def _monitor(self) -> None:
        process = self._process
        if process is None:
            return
        returncode = await process.wait()
        async with self._lock:
            self._returncode = returncode
            self._ended_at = utc_now()
            progress = self._process_progress()
            if self._pause_requested and returncode in {0, -signal.SIGINT, 130}:
                self._state = "paused"
            elif returncode == 0 and (progress or {}).get("phase") == "paused":
                self._state = "paused"
            elif returncode == 0:
                self._state = "completed"
            else:
                self._state = "error"
                self._last_error = self._process_error_message(progress, returncode)
            self._process = None
            if self._log_handle is not None:
                self._log_handle.write(
                    f"[{utc_now()}] END state={self._state} returncode={returncode}\n"
                )
                self._log_handle.close()
                self._log_handle = None
            self._write_controller()

    async def pause(self, timeout: float = 45.0) -> dict[str, Any]:
        async with self._lock:
            if not self.running or self._process is None:
                if self._state in {"paused", "completed", "stopped", "error"}:
                    return self.status()
                raise RuntimeError("采集任务当前没有运行")
            process = self._process
            self._pause_requested = True
            self._state = "pausing"
            process.send_signal(signal.SIGINT)
            self._write_controller()
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            process.terminate()
            await process.wait()
        if self._monitor_task is not None:
            await self._monitor_task
        return self.status()

    async def shutdown(self) -> None:
        if self.running:
            try:
                await self.pause(timeout=20.0)
            except Exception:
                if self._process is not None and self._process.returncode is None:
                    self._process.kill()
                    await self._process.wait()

    def acknowledge_identity_reset(self) -> None:
        """Mark the current failed keyword as manually reset and resumable."""
        run_dir = self._active_run_dir
        if run_dir is None:
            return
        progress_path = run_dir / "progress.json"
        progress = self._read_json(progress_path)
        if not progress:
            return
        keyword = str(progress.get("current_keyword") or "")
        keyword_index = str(progress.get("current_keyword_index") or "")
        checkpoint_path = None
        for candidate in run_dir.glob(f"{keyword_index}-*.json"):
            payload = self._read_json(candidate)
            if payload and payload.get("keyword") == keyword:
                checkpoint_path = candidate
                break
        if checkpoint_path is not None:
            checkpoint = self._read_json(checkpoint_path) or {}
            checkpoint["last_event"] = "identity_reset"
            checkpoint["identity_resets"] = int(
                checkpoint.get("identity_resets") or 0
            ) + 1
            checkpoint["updated_at"] = utc_now()
            runs = checkpoint.get("runs") or []
            if runs:
                runs[-1]["identity_reset_handled"] = True
                runs[-1]["identity_reset_at"] = checkpoint["updated_at"]
            temporary = checkpoint_path.with_suffix(checkpoint_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(checkpoint, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(checkpoint_path)

        aggregate_path = run_dir / "aggregate.json"
        aggregate = self._read_json(aggregate_path)
        if aggregate:
            for metric in aggregate.get("metrics") or []:
                if metric.get("keyword") == keyword:
                    metric["identity_resets"] = int(
                        metric.get("identity_resets") or 0
                    ) + 1
            temporary = aggregate_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(aggregate, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(aggregate_path)

        progress["phase"] = "paused"
        progress["event"] = "identity_reset"
        progress["updated_at"] = utc_now()
        progress["total_identity_resets"] = int(
            progress.get("total_identity_resets") or 0
        ) + 1
        progress["details"] = {"manual": True, "ready_to_resume": True}
        temporary = progress_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(progress_path)
        self._state = "paused"
        self._last_error = None
        self._write_controller()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _process_progress(self) -> dict[str, Any] | None:
        if self._config.get("stage") == "comments":
            return self._read_json(self.comment_checkpoint_path)
        if self._active_run_dir is None:
            return None
        return self._read_json(self._active_run_dir / "progress.json")

    def _comment_status(self) -> dict[str, Any] | None:
        checkpoint = self._read_json(self.comment_checkpoint_path)
        if checkpoint is None:
            return None
        results = checkpoint.get("results") or {}
        unresolved = [
            {
                "video_id": video_id,
                "status": result.get("status"),
                "attempts": int(result.get("attempts") or 0),
                "error": result.get("error"),
                "intercept_result": result.get("intercept_result"),
                "queried_at": result.get("queried_at"),
            }
            for video_id, result in results.items()
            if not isinstance(result.get("comment_count"), int)
            and result.get("status") != "unavailable_null_count"
            and result.get("attempts")
        ]
        unresolved.sort(key=lambda row: str(row.get("queried_at") or ""), reverse=True)
        stats = dict(checkpoint.get("stats") or {})
        source = int(stats.get("source") or checkpoint.get("source_video_count") or 0)
        resolved = int(stats.get("resolved") or 0)
        completed = int(
            stats.get("completed")
            or resolved + int(stats.get("unavailable") or 0)
        )
        return {
            "phase": checkpoint.get("phase"),
            "event": checkpoint.get("event"),
            "started_at": checkpoint.get("started_at"),
            "updated_at": checkpoint.get("updated_at"),
            "threshold": checkpoint.get("threshold", 50),
            "stats": stats,
            "details": checkpoint.get("details") or {},
            "progress_percent": round((completed / source * 100), 2) if source else 0,
            "query_attempts": sum(
                int(result.get("attempts") or 0) for result in results.values()
            ),
            "recent_errors": unresolved[:12],
            "checkpoint_path": str(self.comment_checkpoint_path),
            "output_path": str(self.comment_output_path),
        }

    def _log_tail(self, limit: int = 24) -> list[str]:
        try:
            return self.log_path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []

    def _keyword_library_count(self) -> int:
        try:
            with self.keyword_file.open("r", encoding="utf-8") as handle:
                return max(0, sum(1 for _ in handle) - 1)
        except OSError:
            return 0

    def status(self) -> dict[str, Any]:
        progress = None
        metrics: list[dict[str, Any]] = []
        run_unique = 0
        if self._active_run_dir is not None:
            progress = self._read_json(self._active_run_dir / "progress.json")
            aggregate = self._read_json(self._active_run_dir / "aggregate.json") or {}
            metrics = list(aggregate.get("metrics") or [])
            run_unique = len(aggregate.get("videos") or [])
        master = self._read_json(self.master_path) or {}
        comment_progress = self._comment_status()
        return {
            "state": self._state,
            "running": self.running,
            "pid": self._process.pid if self.running and self._process else None,
            "active_run_dir": str(self._active_run_dir)
            if self._active_run_dir
            else None,
            "started_at": self._started_at,
            "ended_at": self._ended_at,
            "last_error": self._last_error,
            "returncode": self._returncode,
            "config": self._config,
            "progress": progress,
            "metrics": metrics,
            "run_unique_links": run_unique,
            "master_unique_links": int(master.get("video_count") or 0),
            "keyword_library_count": self._keyword_library_count(),
            "comment_progress": comment_progress,
            "log_tail": self._log_tail(),
        }
