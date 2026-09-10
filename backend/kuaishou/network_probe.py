"""CDP-based network recorder used to discover Kuaishou Web data APIs.

The recorder deliberately captures the data channel rather than scraping
rendered text.  It records request/response metadata for every resource and
stores response bodies for data-oriented resource types (Fetch/XHR/etc.).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import re
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Coroutine
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .response_parser import KuaishouResponseParser, omit_comment_content

logger = logging.getLogger("kuaishou.probe")

_SECRET_NAME_RE = re.compile(
    r"(?:authorization|cookie|token|secret|password|passwd|session|credential|"
    r"access[_-]?key|api[_-]?key|ticket|kww|hxfalcon)",
    re.IGNORECASE,
)
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_BODY_RESOURCE_TYPES = frozenset(
    {"XHR", "Fetch", "Document", "EventSource", "Other"}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def redact_headers(headers: dict[str, Any] | None) -> dict[str, Any]:
    """Return headers with authentication material removed."""
    if not headers:
        return {}
    return {
        str(name): "[REDACTED]" if _SECRET_NAME_RE.search(str(name)) else value
        for name, value in headers.items()
    }


def redact_value(value: Any, parent_key: str = "") -> Any:
    """Recursively redact common secret-bearing JSON/form fields."""
    if parent_key and _SECRET_NAME_RE.search(parent_key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(key): redact_value(item, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_url(url: str) -> str:
    """Redact sensitive query parameters while retaining endpoint identity."""
    if url.startswith("data:"):
        header, separator, _ = url.partition(",")
        return f"{header}{separator}[DATA_URL_{len(url)}_CHARS]"
    if url.startswith("blob:"):
        return "blob:[REDACTED]"
    try:
        parts = urlsplit(url)
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            query.append((key, "[REDACTED]" if _SECRET_NAME_RE.search(key) else value))
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
    except Exception:
        return url


def endpoint_key(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme in {"data", "blob"}:
            return f"{parts.scheme}:"
        return f"{parts.netloc}{parts.path}"
    except Exception:
        return url


def parse_post_data(raw: str | None, max_bytes: int) -> tuple[Any, list[str]]:
    """Parse and redact POST data while extracting GraphQL operation names."""
    if not raw:
        return None, []
    encoded = raw.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        return {
            "truncated": True,
            "original_bytes": len(encoded),
            "preview": raw[:4096],
        }, []

    operations: list[str] = []
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        try:
            pairs = parse_qsl(raw, keep_blank_values=True)
        except ValueError:
            return raw, []
        if not pairs:
            return raw, []
        form: dict[str, Any] = {}
        for key, value in pairs:
            safe_value: Any = "[REDACTED]" if _SECRET_NAME_RE.search(key) else value
            if key in form:
                current = form[key]
                form[key] = current + [safe_value] if isinstance(current, list) else [current, safe_value]
            else:
                form[key] = safe_value
        operation = form.get("operationName")
        if isinstance(operation, str):
            operations.append(operation)
        return form, operations

    documents = parsed if isinstance(parsed, list) else [parsed]
    for document in documents:
        if isinstance(document, dict):
            operation = document.get("operationName")
            if isinstance(operation, str) and operation:
                operations.append(operation)
    return redact_value(parsed), operations


@dataclass(frozen=True)
class ProbeConfig:
    root_dir: Path
    keyword: str
    profile_id: str = "kuaishou"
    max_body_bytes: int = 25 * 1024 * 1024
    max_post_data_bytes: int = 2 * 1024 * 1024
    body_resource_types: frozenset[str] = _BODY_RESOURCE_TYPES
    recent_event_limit: int = 100


@dataclass
class _ResponseState:
    session: Any
    request_id: str
    resource_type: str
    url: str
    mime_type: str
    status: int
    headers: dict[str, Any] = field(default_factory=dict)


class NetworkProbe:
    """Record Chromium network traffic for one labelled capture session."""

    def __init__(self, config: ProbeConfig):
        self.config = config
        safe_keyword = _SAFE_FILENAME_RE.sub("-", config.keyword).strip("-._") or "search"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.session_id = f"{stamp}-{safe_keyword}"
        self.session_dir = config.root_dir / self.session_id
        suffix = 1
        while self.session_dir.exists():
            self.session_id = f"{stamp}-{safe_keyword}-{suffix}"
            self.session_dir = config.root_dir / self.session_id
            suffix += 1
        self.bodies_dir = self.session_dir / "bodies"
        self.events_path = self.session_dir / "events.jsonl"
        self.summary_path = self.session_dir / "summary.json"

        self.started_at = utc_now()
        self.ended_at: str | None = None
        self.active = False
        self._sequence = 0
        self._write_lock = asyncio.Lock()
        self._sessions: list[Any] = []
        self._attached_page_ids: set[int] = set()
        self._pending: set[asyncio.Task[Any]] = set()
        self._responses: dict[tuple[int, str], _ResponseState] = {}
        self._request_operations: dict[tuple[int, str], list[str]] = {}
        self._request_post_data: dict[tuple[int, str], Any] = {}
        self._event_counts: Counter[str] = Counter()
        self._resource_counts: Counter[str] = Counter()
        self._endpoint_counts: Counter[str] = Counter()
        self._operation_counts: Counter[str] = Counter()
        self._body_count = 0
        self._body_bytes = 0
        self._errors: deque[str] = deque(maxlen=50)
        self._risk_controls: deque[dict[str, Any]] = deque(maxlen=20)
        self._recent_events: deque[dict[str, Any]] = deque(
            maxlen=config.recent_event_limit
        )
        self._parser = KuaishouResponseParser(config.keyword)
        self._comment_waiters: dict[str, list[asyncio.Future[int]]] = {}

    async def start(self) -> None:
        self.bodies_dir.mkdir(parents=True, exist_ok=False)
        self.active = True
        manifest = {
            "schema_version": 1,
            "session_id": self.session_id,
            "keyword": self.config.keyword,
            "profile_id": self.config.profile_id,
            "started_at": self.started_at,
            "recorder": "cdp-network",
            "max_body_bytes": self.config.max_body_bytes,
            "max_post_data_bytes": self.config.max_post_data_bytes,
            "body_resource_types": sorted(self.config.body_resource_types),
            "redaction": "headers, query parameters and structured secret fields",
        }
        await asyncio.to_thread(
            (self.session_dir / "manifest.json").write_text,
            json.dumps(manifest, ensure_ascii=False, indent=2),
            "utf-8",
        )
        await self.mark("probe_started", keyword=self.config.keyword)

    def _schedule(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        if not self.active:
            coroutine.close()
            return
        task = asyncio.create_task(coroutine)
        self._pending.add(task)

        def done(completed: asyncio.Task[Any]) -> None:
            self._pending.discard(completed)
            try:
                error = completed.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                message = f"{type(error).__name__}: {error}"
                self._errors.append(message)
                logger.warning("Network probe event failed: %s", message)

        task.add_done_callback(done)

    async def attach(self, page: Any) -> None:
        """Attach a CDP Network session before the page navigates."""
        if not self.active or id(page) in self._attached_page_ids:
            return
        self._attached_page_ids.add(id(page))
        session = await page.context.new_cdp_session(page)
        self._sessions.append(session)
        await session.send(
            "Network.enable",
            {
                "maxTotalBufferSize": max(self.config.max_body_bytes * 4, 100_000_000),
                "maxResourceBufferSize": self.config.max_body_bytes,
                "maxPostDataSize": self.config.max_post_data_bytes,
            },
        )
        session.on(
            "Network.requestWillBeSent",
            lambda params, current=session: self._schedule(
                self._on_request(current, params)
            ),
        )
        session.on(
            "Network.requestWillBeSentExtraInfo",
            lambda params, current=session: self._schedule(
                self._on_request_extra(current, params)
            ),
        )
        session.on(
            "Network.responseReceived",
            lambda params, current=session: self._schedule(
                self._on_response(current, params)
            ),
        )
        session.on(
            "Network.loadingFinished",
            lambda params, current=session: self._schedule(
                self._on_loading_finished(current, params)
            ),
        )
        session.on(
            "Network.loadingFailed",
            lambda params, current=session: self._schedule(
                self._on_loading_failed(current, params)
            ),
        )
        session.on(
            "Network.webSocketCreated",
            lambda params, current=session: self._schedule(
                self._on_websocket_created(current, params)
            ),
        )
        session.on(
            "Network.webSocketFrameReceived",
            lambda params, current=session: self._schedule(
                self._on_websocket_frame(current, params, "received")
            ),
        )
        session.on(
            "Network.webSocketFrameSent",
            lambda params, current=session: self._schedule(
                self._on_websocket_frame(current, params, "sent")
            ),
        )
        await self._append_event(
            "page_attached",
            page_url=redact_url(getattr(page, "url", "")),
            cdp_session=id(session),
        )

    async def mark(self, action: str, **details: Any) -> None:
        await self._append_event("action", action=action, details=redact_value(details))

    async def _on_request(self, session: Any, params: dict[str, Any]) -> None:
        request = params.get("request") or {}
        request_id = str(params.get("requestId", ""))
        resource_type = str(params.get("type") or "Other")
        url = str(request.get("url") or "")
        post_data, operations = parse_post_data(
            request.get("postData"), self.config.max_post_data_bytes
        )
        key = (id(session), request_id)
        self._request_operations[key] = operations
        self._request_post_data[key] = post_data
        for operation in operations:
            self._operation_counts[operation] += 1
        self._resource_counts[resource_type] += 1
        self._endpoint_counts[endpoint_key(url)] += 1
        initiator = params.get("initiator") or {}
        await self._append_event(
            "request",
            request_id=request_id,
            resource_type=resource_type,
            method=request.get("method"),
            url=redact_url(url),
            document_url=redact_url(str(params.get("documentURL") or "")),
            headers=redact_headers(request.get("headers")),
            post_data=post_data,
            operation_names=operations,
            initiator_type=initiator.get("type"),
            has_user_gesture=bool(params.get("hasUserGesture", False)),
            redirect=bool(params.get("redirectResponse")),
        )

    async def _on_request_extra(self, session: Any, params: dict[str, Any]) -> None:
        await self._append_event(
            "request_extra",
            request_id=str(params.get("requestId", "")),
            headers=redact_headers(params.get("headers")),
            associated_cookies="[REDACTED]" if params.get("associatedCookies") else [],
        )

    async def _on_response(self, session: Any, params: dict[str, Any]) -> None:
        response = params.get("response") or {}
        request_id = str(params.get("requestId", ""))
        resource_type = str(params.get("type") or "Other")
        url = str(response.get("url") or "")
        state = _ResponseState(
            session=session,
            request_id=request_id,
            resource_type=resource_type,
            url=url,
            mime_type=str(response.get("mimeType") or ""),
            status=int(response.get("status") or 0),
            headers=response.get("headers") or {},
        )
        self._responses[(id(session), request_id)] = state
        intercept_result = next(
            (
                str(value)
                for name, value in state.headers.items()
                if str(name).lower() == "intercept-result"
            ),
            None,
        )
        if intercept_result and "risk-control" in intercept_result.lower():
            risk_event = {
                "request_id": request_id,
                "url": redact_url(url),
                "intercept_result": intercept_result,
                "status": state.status,
                "at": utc_now(),
            }
            self._risk_controls.append(risk_event)
            await self._append_event("risk_control", **risk_event)
        await self._append_event(
            "response",
            request_id=request_id,
            resource_type=resource_type,
            url=redact_url(url),
            status=state.status,
            status_text=response.get("statusText"),
            mime_type=state.mime_type,
            protocol=response.get("protocol"),
            from_disk_cache=bool(response.get("fromDiskCache", False)),
            from_service_worker=bool(response.get("fromServiceWorker", False)),
            encoded_data_length=response.get("encodedDataLength"),
            headers=redact_headers(state.headers),
            operation_names=self._request_operations.get(
                (id(session), request_id), []
            ),
        )

    async def _on_loading_finished(
        self, session: Any, params: dict[str, Any]
    ) -> None:
        request_id = str(params.get("requestId", ""))
        key = (id(session), request_id)
        response = self._responses.pop(key, None)
        if response is None:
            return
        if response.resource_type not in self.config.body_resource_types:
            return
        encoded_length = int(params.get("encodedDataLength") or 0)
        if encoded_length > self.config.max_body_bytes:
            await self._append_event(
                "body_skipped",
                request_id=request_id,
                url=redact_url(response.url),
                reason="encoded body exceeds configured limit",
                encoded_data_length=encoded_length,
            )
            self._request_operations.pop(key, None)
            self._request_post_data.pop(key, None)
            return

        try:
            result = await session.send(
                "Network.getResponseBody", {"requestId": request_id}
            )
        except Exception as exc:
            await self._append_event(
                "body_error",
                request_id=request_id,
                url=redact_url(response.url),
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        body_text = result.get("body", "")
        base64_encoded = bool(result.get("base64Encoded", False))
        if base64_encoded:
            try:
                body = base64.b64decode(body_text, validate=False)
            except Exception as exc:
                await self._append_event(
                    "body_error",
                    request_id=request_id,
                    url=redact_url(response.url),
                    error=f"base64 decode failed: {exc}",
                )
                return
        else:
            body = str(body_text).encode("utf-8", errors="replace")

        original_size = len(body)
        truncated = original_size > self.config.max_body_bytes
        if truncated:
            body = body[: self.config.max_body_bytes]
        operations = self._request_operations.get(key, [])
        parsed_payload: Any = None
        if "json" in response.mime_type.lower() or body.lstrip().startswith((b"{", b"[")):
            try:
                parsed_payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed_payload = None
        body_info = await self._save_body(
            response,
            body,
            original_size,
            truncated,
            parsed_payload=parsed_payload,
            operation_names=operations,
        )
        await self._append_event(
            "body_saved",
            request_id=request_id,
            url=redact_url(response.url),
            resource_type=response.resource_type,
            mime_type=response.mime_type,
            operation_names=operations,
            **body_info,
        )
        if parsed_payload is not None:
            data_events = self._parser.consume(
                url=response.url,
                operation_names=operations,
                request_data=self._request_post_data.get(key),
                response_data=parsed_payload,
            )
            for data_event in data_events:
                await self._append_event("data_detected", **data_event)
                if data_event.get("data_kind") == "comment_count":
                    video_id = str(data_event.get("video_id") or "")
                    count = data_event.get("comment_count")
                    if video_id and isinstance(count, int):
                        self._resolve_comment_waiters(video_id, count)
        self._request_operations.pop(key, None)
        self._request_post_data.pop(key, None)

    async def _save_body(
        self,
        response: _ResponseState,
        body: bytes,
        original_size: int,
        truncated: bool,
        parsed_payload: Any = None,
        operation_names: list[str] | None = None,
    ) -> dict[str, Any]:
        safe_request_id = _SAFE_FILENAME_RE.sub("_", response.request_id)
        content_type = response.mime_type.lower()
        saved_data = body
        extension = ".bin"
        parsed_json = False
        if parsed_payload is not None:
            try:
                value = parsed_payload
                if operation_names and "commentListQuery" in operation_names:
                    value = omit_comment_content(value)
                saved_data = json.dumps(
                    redact_value(value), ensure_ascii=False, indent=2
                ).encode("utf-8")
                extension = ".json"
                parsed_json = True
            except (TypeError, ValueError):
                extension = ".txt" if not truncated else ".bin"
        elif "json" in content_type or body.lstrip().startswith((b"{", b"[")):
            extension = ".txt" if not truncated else ".bin"
        elif content_type.startswith("text/"):
            extension = ".txt"

        filename = f"{safe_request_id}{extension}"
        path = self.bodies_dir / filename
        serial = 1
        while path.exists():
            filename = f"{safe_request_id}-{serial}{extension}"
            path = self.bodies_dir / filename
            serial += 1
        await asyncio.to_thread(path.write_bytes, saved_data)
        self._body_count += 1
        self._body_bytes += len(saved_data)
        return {
            "file": f"bodies/{filename}",
            "original_bytes": original_size,
            "saved_bytes": len(saved_data),
            "truncated": truncated,
            "parsed_json": parsed_json,
            "sha256": hashlib.sha256(saved_data).hexdigest(),
        }

    async def _on_loading_failed(
        self, session: Any, params: dict[str, Any]
    ) -> None:
        request_id = str(params.get("requestId", ""))
        key = (id(session), request_id)
        self._responses.pop(key, None)
        self._request_operations.pop(key, None)
        self._request_post_data.pop(key, None)
        await self._append_event(
            "loading_failed",
            request_id=request_id,
            error_text=params.get("errorText"),
            canceled=bool(params.get("canceled", False)),
            blocked_reason=params.get("blockedReason"),
        )

    async def _on_websocket_created(
        self, session: Any, params: dict[str, Any]
    ) -> None:
        await self._append_event(
            "websocket_created",
            request_id=str(params.get("requestId", "")),
            url=redact_url(str(params.get("url") or "")),
            initiator=(params.get("initiator") or {}).get("type"),
        )

    async def _on_websocket_frame(
        self, session: Any, params: dict[str, Any], direction: str
    ) -> None:
        frame = params.get("response") or {}
        payload = frame.get("payloadData", "")
        payload_text = str(payload)
        truncated = len(payload_text.encode("utf-8", errors="replace")) > self.config.max_body_bytes
        if truncated:
            payload_text = payload_text[: self.config.max_body_bytes]
        await self._append_event(
            "websocket_frame",
            request_id=str(params.get("requestId", "")),
            direction=direction,
            opcode=frame.get("opcode"),
            mask=frame.get("mask"),
            payload=payload_text,
            truncated=truncated,
        )

    async def _append_event(self, event_type: str, **payload: Any) -> None:
        event = {
            "schema_version": 1,
            "type": event_type,
            "at": utc_now(),
            **payload,
        }
        async with self._write_lock:
            self._sequence += 1
            event["seq"] = self._sequence
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            self._event_counts[event_type] += 1
            recent = {
                key: value
                for key, value in event.items()
                if key
                in {
                    "seq",
                    "type",
                    "at",
                    "request_id",
                    "resource_type",
                    "method",
                    "url",
                    "status",
                    "mime_type",
                    "operation_names",
                    "file",
                    "action",
                    "error",
                    "data_kind",
                    "video_id",
                    "title",
                    "author_name",
                    "comment_count",
                    "received",
                    "unique_videos",
                    "pcursor",
                }
            }
            self._recent_events.append(recent)

    def status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "keyword": self.config.keyword,
            "profile_id": self.config.profile_id,
            "active": self.active,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "session_dir": str(self.session_dir),
            "event_counts": dict(self._event_counts),
            "resource_counts": dict(self._resource_counts),
            "body_count": self._body_count,
            "body_bytes": self._body_bytes,
            "operation_counts": dict(self._operation_counts.most_common(30)),
            "top_endpoints": dict(self._endpoint_counts.most_common(30)),
            "errors": list(self._errors),
            "risk_controls": list(self._risk_controls),
            "extracted": self._parser.status(),
            "recent_events": list(self._recent_events),
        }

    def _resolve_comment_waiters(self, video_id: str, count: int) -> None:
        for waiter in self._comment_waiters.pop(video_id, []):
            if not waiter.done():
                waiter.set_result(count)

    async def wait_for_comment_count(
        self, video_id: str, timeout: float = 15.0
    ) -> int:
        existing = self._parser.videos.get(video_id)
        if existing is not None and existing.comment_count is not None:
            return existing.comment_count
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[int] = loop.create_future()
        self._comment_waiters.setdefault(video_id, []).append(waiter)
        try:
            return await asyncio.wait_for(waiter, timeout=timeout)
        finally:
            waiters = self._comment_waiters.get(video_id, [])
            if waiter in waiters:
                waiters.remove(waiter)
            if not waiters:
                self._comment_waiters.pop(video_id, None)

    async def stop(self, error: str | None = None) -> dict[str, Any]:
        if not self.active and self.ended_at:
            return self.status()
        if self.active:
            await self.mark("probe_stopping", error=error)
        self.active = False

        for waiters in self._comment_waiters.values():
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
        self._comment_waiters.clear()

        pending = list(self._pending)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        for session in self._sessions:
            try:
                result = session.detach()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass
        self._sessions.clear()
        self.ended_at = utc_now()
        summary = self.status()
        if error:
            summary["terminal_error"] = error
        await asyncio.to_thread(
            self.summary_path.write_text,
            json.dumps(summary, ensure_ascii=False, indent=2),
            "utf-8",
        )
        return summary
