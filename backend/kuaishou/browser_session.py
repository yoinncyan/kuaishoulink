"""Lifecycle management for one persistent Kuaishou browser and its probe."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from backend.config import Settings

from .network_probe import NetworkProbe, ProbeConfig, utc_now

logger = logging.getLogger("kuaishou.browser")

KUAISHOU_HOME_URL = "https://www.kuaishou.com/"
KUAISHOU_SEARCH_URL = "https://www.kuaishou.com/search/{keyword}"
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,80}$")


class BrowserProbeManager:
    """Own exactly one persistent browser context and one optional probe.

    Browser and probe lifetimes are deliberately independent.  Operators can
    launch the browser, complete Kuaishou login/captcha, and retain that trusted
    profile before starting a capture.  Stopping a probe does not discard the
    browser session or its cookies.
    """

    def __init__(
        self,
        settings: Settings,
        launch_context: Callable[..., Any] | None = None,
    ):
        self.settings = settings
        self._launch_context = launch_context
        self._context: Any | None = None
        self._page: Any | None = None
        self._probe: NetworkProbe | None = None
        self._lock = asyncio.Lock()
        self._browser_state = "stopped"
        self._probe_state = "stopped"
        self._last_error: str | None = None
        self._page_url: str | None = None
        self._browser_started_at: str | None = None
        self._probe_started_at: str | None = None

    async def _resolve_launcher(self) -> Callable[..., Any]:
        if self._launch_context is not None:
            return self._launch_context
        from cloakbrowser import launch_persistent_context_async

        return launch_persistent_context_async

    @staticmethod
    def search_url(keyword: str) -> str:
        normalized = keyword.strip()
        if not normalized:
            raise ValueError("keyword cannot be empty")
        return KUAISHOU_SEARCH_URL.format(keyword=quote(normalized, safe=""))

    async def launch_browser(self) -> dict[str, Any]:
        """Launch the persistent browser without starting a network probe."""
        async with self._lock:
            if self._context is not None and self._browser_state == "running":
                return self.status()
            if self._browser_state in {"starting", "stopping"}:
                raise RuntimeError("browser transition is already in progress")
            self._browser_state = "starting"
            self._last_error = None

        self.settings.ensure_directories()
        try:
            launcher = await self._resolve_launcher()
            launch_options: dict[str, Any] = {
                "user_data_dir": self.settings.profile_dir,
                "headless": self.settings.headless,
                "locale": self.settings.browser_locale,
                "timezone": self.settings.browser_timezone,
                "geoip": self.settings.browser_geoip,
                "humanize": False,
                "license_key": self.settings.license_key,
                "release_channel": self.settings.release_channel,
                "proxy": self.settings.proxy,
                "args": ["--disable-dev-shm-usage"],
            }
            if not self.settings.headless:
                launch_options["viewport"] = {
                    "width": self.settings.browser_width,
                    "height": self.settings.browser_height,
                }
            context = await launcher(**launch_options)
            self._context = context

            def observe_new_page(page: Any) -> None:
                self._page = page
                probe = self._probe
                if probe is not None and probe.active:
                    probe._schedule(probe.attach(page))

            context.on("page", observe_new_page)
            pages = list(context.pages)
            self._page = pages[0] if pages else await context.new_page()
            self._page_url = getattr(self._page, "url", None)
            self._browser_started_at = utc_now()
            async with self._lock:
                self._browser_state = "running"
            return self.status()
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self._last_error = message
            self._context = None
            self._page = None
            async with self._lock:
                self._browser_state = "error"
            logger.exception("Could not launch persistent Kuaishou browser")
            raise

    async def open_login(self) -> dict[str, Any]:
        """Open Kuaishou home so the operator can establish the saved profile."""
        return await self.navigate(KUAISHOU_HOME_URL)

    async def navigate(self, url: str) -> dict[str, Any]:
        """Navigate the persistent browser to an explicitly allowed Kuaishou URL."""
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "www.kuaishou.com":
            raise ValueError("only https://www.kuaishou.com URLs are allowed")
        await self.launch_browser()
        page = self._require_page()
        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.settings.navigation_timeout_ms,
            )
        except Exception:
            # Kuaishou can keep the document request open while still rendering
            # a usable login/captcha page.  Preserve that page for the operator.
            if not str(getattr(page, "url", "")).startswith("https://www.kuaishou.com"):
                raise
            logger.warning("Kuaishou navigation timed out after reaching the site")
        self._page_url = getattr(page, "url", url)
        return self.status()

    async def start(self, keyword: str) -> dict[str, Any]:
        """Start a fresh network capture and navigate the saved profile to search."""
        keyword = keyword.strip()
        target_url = self.search_url(keyword)
        async with self._lock:
            if self._probe_state in {"starting", "running", "stopping"}:
                raise RuntimeError("a probe session is already active")
            self._probe_state = "starting"
            self._last_error = None
            self._probe_started_at = utc_now()

        probe = NetworkProbe(
            ProbeConfig(
                root_dir=self.settings.captures_dir,
                keyword=keyword,
                max_body_bytes=self.settings.max_body_bytes,
                max_post_data_bytes=self.settings.max_post_data_bytes,
            )
        )
        await probe.start()
        self._probe = probe

        try:
            await self.launch_browser()
            context = self._require_context()
            page = self._require_page()
            for existing_page in list(context.pages):
                await probe.attach(existing_page)

            await probe.mark("navigate_search", keyword=keyword, url=target_url)
            try:
                await page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.navigation_timeout_ms,
                )
            except Exception:
                if not str(getattr(page, "url", "")).startswith(
                    "https://www.kuaishou.com/search/"
                ):
                    raise
                logger.warning("Search navigation timed out after reaching %s", page.url)
            self._page_url = page.url
            async with self._lock:
                self._probe_state = "running"
            return self.status()
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            logger.exception("Could not start Kuaishou probe")
            self._last_error = message
            await probe.stop(error=message)
            async with self._lock:
                self._probe_state = "error"
            raise

    async def mark(self, action: str, details: dict[str, Any] | None = None) -> None:
        if self._probe is None or not self._probe.active:
            raise RuntimeError("no active probe session")
        await self._probe.mark(action, **(details or {}))

    async def inspect_video_comments(
        self, video_id: str, timeout_seconds: float = 15.0
    ) -> dict[str, Any]:
        """Load one detail page and return its first comment-count response."""
        video_id = video_id.strip()
        if not _VIDEO_ID_RE.fullmatch(video_id):
            raise ValueError("invalid Kuaishou video ID")
        probe = self._probe
        if probe is None or not probe.active:
            raise RuntimeError("start a probe before inspecting video comments")
        page = self._require_page()
        detail_url = f"https://www.kuaishou.com/short-video/{video_id}"
        await probe.mark("inspect_video_comments", video_id=video_id, url=detail_url)

        waiter = asyncio.create_task(
            probe.wait_for_comment_count(video_id, timeout=timeout_seconds)
        )
        try:
            try:
                await page.goto(
                    detail_url,
                    wait_until="commit",
                    timeout=self.settings.navigation_timeout_ms,
                )
            except Exception:
                if not str(getattr(page, "url", "")).startswith(detail_url):
                    raise
            self._page_url = page.url
            count = await waiter
            await probe.mark(
                "comment_count_resolved", video_id=video_id, comment_count=count
            )
            return {
                "video_id": video_id,
                "video_url": detail_url,
                "comment_count": count,
                "qualifies_default": count > 50,
            }
        finally:
            if not waiter.done():
                waiter.cancel()
            # Stop autoplay and comment pagination immediately.  The next item
            # can navigate directly from this neutral page.
            try:
                await page.goto("about:blank", wait_until="commit", timeout=5_000)
                self._page_url = page.url
            except Exception:
                pass

    async def direct_comment_count(self, video_id: str) -> dict[str, Any]:
        """Diagnostic: request only the count from inside the Kuaishou page.

        This intentionally uses the page's own origin, cookies and native fetch.
        It does not synthesize private headers.  The result tells us whether the
        site's request-signing layer is mandatory for this GraphQL operation.
        """
        video_id = video_id.strip()
        if not _VIDEO_ID_RE.fullmatch(video_id):
            raise ValueError("invalid Kuaishou video ID")
        page = self._require_page()
        if not str(getattr(page, "url", "")).startswith("https://www.kuaishou.com"):
            raise RuntimeError("open a kuaishou.com page before the direct query")
        result = await page.evaluate(
            """
            async ({ videoId }) => {
              const payload = {
                operationName: 'commentListQuery',
                variables: { photoId: videoId, pcursor: '' },
                query: `query commentListQuery($photoId: String, $pcursor: String) {
                  visionCommentList(photoId: $photoId, pcursor: $pcursor) {
                    commentCount
                    commentCountV2
                    __typename
                  }
                }`,
              };
              const response = await fetch('/graphql', {
                method: 'POST',
                credentials: 'include',
                headers: {
                  'accept': 'application/json',
                  'content-type': 'application/json',
                },
                body: JSON.stringify(payload),
              });
              return {
                ok: response.ok,
                status: response.status,
                contentType: response.headers.get('content-type'),
                interceptResult: response.headers.get('intercept-result'),
                body: await response.text(),
              };
            }
            """,
            {"videoId": video_id},
        )
        body_text = result.get("body", "") if isinstance(result, dict) else ""
        try:
            import json

            body = json.loads(body_text)
        except (TypeError, json.JSONDecodeError):
            body = None
        count = None
        if isinstance(body, dict):
            comment_feed = ((body.get("data") or {}).get("visionCommentList") or {})
            if isinstance(comment_feed, dict):
                from .response_parser import parse_count

                count = parse_count(comment_feed.get("commentCountV2"))
                if count is None:
                    count = parse_count(comment_feed.get("commentCount"))
        return {
            "video_id": video_id,
            "transport_ok": bool(result.get("ok")) if isinstance(result, dict) else False,
            "http_status": result.get("status") if isinstance(result, dict) else None,
            "content_type": result.get("contentType") if isinstance(result, dict) else None,
            "intercept_result": result.get("interceptResult") if isinstance(result, dict) else None,
            "comment_count": count,
            "response": body,
        }

    async def stop(self) -> dict[str, Any]:
        """Stop only the capture; retain the persistent browser and login."""
        async with self._lock:
            if self._probe_state not in {"running", "error", "starting"}:
                raise RuntimeError("no active probe session")
            self._probe_state = "stopping"
        summary: dict[str, Any] = {}
        if self._probe is not None:
            summary = await self._probe.stop(error=self._last_error)
        async with self._lock:
            self._probe_state = "stopped"
        return summary

    async def close_browser(self) -> dict[str, Any]:
        """Stop capture if needed, then persist and close the browser context."""
        if self._probe_state in {"starting", "running", "error"}:
            await self.stop()
        async with self._lock:
            if self._context is None:
                self._browser_state = "stopped"
                return self.status()
            self._browser_state = "stopping"
            context = self._context
        try:
            await context.close()
        finally:
            self._context = None
            self._page = None
            self._page_url = None
            async with self._lock:
                self._browser_state = "stopped"
        return self.status()

    async def reload_page(self) -> dict[str, Any]:
        page = self._require_page()
        await page.reload(
            wait_until="domcontentloaded",
            timeout=self.settings.navigation_timeout_ms,
        )
        self._page_url = page.url
        return self.status()

    async def scroll_page(self, distance: int = 1800) -> dict[str, Any]:
        """Wheel over the right-hand result pane to trigger the next page."""
        if distance < 100 or distance > 20_000:
            raise ValueError("scroll distance must be between 100 and 20000")
        page = self._require_page()
        viewport = await page.evaluate(
            """
            () => ({ width: window.innerWidth, height: window.innerHeight })
            """,
        )
        width = int(viewport.get("width") or self.settings.browser_width)
        height = int(viewport.get("height") or self.settings.browser_height)
        # Search results occupy the large pane to the right of the navigation
        # rail. (0, 0) and automatic scroll-container scoring both hit the rail.
        target_x = width * 0.72
        target_y = height * 0.72
        await page.mouse.move(target_x, target_y)
        steps = max(1, min(12, (distance + 899) // 900))
        per_step = distance / steps
        for _ in range(steps):
            await page.mouse.wheel(0, per_step)
            await page.wait_for_timeout(120)
        scroll_result = {
            "target": "right-content-pane",
            "x": target_x,
            "y": target_y,
            "distance": distance,
            "steps": steps,
            "viewport": {"width": width, "height": height},
        }
        if self._probe is not None and self._probe.active:
            await self._probe.mark("scroll", distance=distance, result=scroll_result)
        payload = self.status()
        payload["scroll"] = scroll_result
        return payload

    def extracted_status(self) -> dict[str, Any]:
        if self._probe is None:
            raise RuntimeError("no probe session is available")
        return self._probe.status()["extracted"]

    async def screenshot(self) -> bytes:
        page = self._require_page()
        return await page.screenshot(type="png", full_page=False, timeout=5_000)

    async def shutdown(self) -> None:
        if self._context is not None:
            try:
                await self.close_browser()
            except Exception:
                logger.exception("Browser shutdown failed")

    def _require_context(self) -> Any:
        if self._context is None:
            raise RuntimeError("browser is not running")
        return self._context

    def _require_page(self) -> Any:
        if self._page is None:
            raise RuntimeError("browser page is not available")
        return self._page

    def status(self) -> dict[str, Any]:
        if self._page is not None:
            self._page_url = getattr(self._page, "url", self._page_url)
        payload: dict[str, Any] = {
            # `state` remains the probe-state alias used by the initial UI/API.
            "state": self._probe_state,
            "probe_state": self._probe_state,
            "browser_state": self._browser_state,
            "started_at": self._probe_started_at,
            "browser_started_at": self._browser_started_at,
            "page_url": self._page_url,
            "last_error": self._last_error,
            "headless": self.settings.headless,
            "browser_locale": self.settings.browser_locale,
            "browser_timezone": self.settings.browser_timezone,
            "browser_geoip": self.settings.browser_geoip,
            "profile_dir": str(self.settings.profile_dir),
        }
        payload["probe"] = self._probe.status() if self._probe is not None else None
        return payload

    def list_sessions(self) -> list[dict[str, Any]]:
        root = self.settings.captures_dir
        if not root.exists():
            return []
        sessions: list[dict[str, Any]] = []
        for directory in sorted(root.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            summary_file = directory / "summary.json"
            manifest_file = directory / "manifest.json"
            source = summary_file if summary_file.exists() else manifest_file
            try:
                import json

                data = json.loads(source.read_text(encoding="utf-8"))
            except Exception:
                data = {"session_id": directory.name}
            data["complete"] = summary_file.exists()
            sessions.append(data)
        return sessions
