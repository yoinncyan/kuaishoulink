"""Lifecycle management for one persistent Kuaishou browser and its probe."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from backend.config import Settings

from .network_probe import NetworkProbe, ProbeConfig, utc_now
from .identity import load_or_create_identity

logger = logging.getLogger("kuaishou.browser")

KUAISHOU_HOME_URL = "https://www.kuaishou.com/"
KUAISHOU_HOME_SEARCH_URL = "https://www.kuaishou.com/?isHome=1&source=SEARCH"
KUAISHOU_SEARCH_URL = "https://www.kuaishou.com/search/{keyword}"
KUAISHOU_SEARCH_PLACEHOLDER = "请输入你要搜索的内容"
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,80}$")


class ProbeStartCancelled(RuntimeError):
    """A concurrent pause superseded an in-flight probe start."""


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
        self._home_page: Any | None = None
        self._probe: NetworkProbe | None = None
        self._lock = asyncio.Lock()
        self._browser_state = "stopped"
        self._probe_state = "stopped"
        self._probe_generation = 0
        self._last_error: str | None = None
        self._page_url: str | None = None
        self._browser_started_at: str | None = None
        self._probe_started_at: str | None = None
        self._fingerprint_seed: int | None = None
        self._identity_id: str | None = None
        self._browser_temp_dir: str | None = None
        self._browser_version: str | None = None
        self._headless_override: bool | None = None
        self._launched_headless: bool | None = None

    def effective_headless(self) -> bool:
        return (
            self._headless_override
            if self._headless_override is not None
            else self.settings.headless
        )

    @staticmethod
    def _page_is_closed(page: Any | None) -> bool:
        if page is None:
            return True
        try:
            return bool(page.is_closed())
        except Exception:
            return False

    async def configure_window_mode(self, open_browser_window: bool) -> dict[str, Any]:
        """Choose whether the next browser launch has a visible app window."""
        async with self._lock:
            if (
                self._probe_state in {"starting", "running", "stopping"}
                and (self._probe is None or not self._probe.active)
            ):
                # A user can close the visible browser while /probe/start is
                # still returning. Normalize the stale state so the next task
                # start can relaunch the same persisted Profile immediately.
                self._probe_generation += 1
                self._probe_state = "stopped"
        desired_headless = not open_browser_window
        if self._probe_state == "error":
            await self.stop()
        if self._probe_state in {"starting", "running", "stopping"}:
            raise RuntimeError("请先停止网络探针再切换浏览器窗口模式")
        if (
            self._context is not None
            and self._browser_state == "running"
            and self._launched_headless != desired_headless
        ):
            await self.close_browser()
        self._headless_override = desired_headless
        payload = self.status()
        payload["window_mode_changed"] = True
        return payload

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
        stale_context = None
        async with self._lock:
            if self._context is not None and self._browser_state == "running":
                if not self._page_is_closed(self._page):
                    return self.status()
                stale_context = self._context
                self._context = None
                self._page = None
                self._home_page = None
                self._page_url = None
                self._launched_headless = None
            elif self._browser_state in {"starting", "stopping"}:
                raise RuntimeError("browser transition is already in progress")
            self._browser_state = "starting"
            self._last_error = None

        if stale_context is not None:
            try:
                await stale_context.close()
            except Exception:
                logger.info("Discarding an already-closed browser context", exc_info=True)

        self.settings.ensure_directories()
        try:
            launcher = await self._resolve_launcher()
            identity = load_or_create_identity(self.settings.identity_file)
            self._fingerprint_seed = identity.fingerprint_seed
            self._identity_id = identity.identity_id
            browser_temp_dir = (
                Path(tempfile.gettempdir())
                / "kuaishou-cloakbrowser"
                / identity.identity_id
            )
            process_temp_dir = browser_temp_dir / "tmp"
            xdg_cache_dir = browser_temp_dir / "xdg-cache"
            process_temp_dir.mkdir(parents=True, exist_ok=True)
            xdg_cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                browser_temp_dir.chmod(0o700)
            except OSError:
                pass
            self._browser_temp_dir = str(browser_temp_dir)
            browser_env = dict(os.environ)
            browser_env.update(
                {
                    "TMPDIR": str(process_temp_dir),
                    "TMP": str(process_temp_dir),
                    "TEMP": str(process_temp_dir),
                    "XDG_CACHE_HOME": str(xdg_cache_dir),
                    "CLOAK_IDENTITY_ID": identity.identity_id,
                }
            )
            browser_version = os.environ.get("CLOAKBROWSER_VERSION") or None
            if browser_version is None and not self.settings.release_channel:
                # Pin the wrapper's installed build. Otherwise every fresh
                # identity starts a background PyPI/GitHub update check while
                # the visible window is still about:blank.
                from cloakbrowser.config import get_chromium_version

                browser_version = get_chromium_version()
            self._browser_version = browser_version
            headless = self.effective_headless()
            launch_options: dict[str, Any] = {
                "user_data_dir": self.settings.profile_dir,
                "headless": headless,
                "locale": self.settings.browser_locale,
                "timezone": self.settings.browser_timezone,
                "geoip": self.settings.browser_geoip,
                "humanize": False,
                "license_key": self.settings.license_key,
                "release_channel": self.settings.release_channel,
                "browser_version": browser_version,
                "proxy": self.settings.proxy,
                "env": browser_env,
                "args": [
                    "--disable-dev-shm-usage",
                    f"--fingerprint={identity.fingerprint_seed}",
                ],
            }
            if not headless:
                launch_options["viewport"] = {
                    "width": self.settings.browser_width,
                    "height": self.settings.browser_height,
                }
            context = await launcher(**launch_options)
            self._context = context
            self._launched_headless = headless
            generation_marker = self.settings.profile_dir / ".cloak-identity.json"
            generation_marker.write_text(
                json.dumps(identity.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

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

    async def _bootstrap_home(self) -> None:
        """Reach the Kuaishou origin using an early commit-level wait.

        Fresh profiles sometimes keep background resources open long enough for
        ``domcontentloaded`` to time out even though the document committed.
        This bootstrap is only a prerequisite; probes are attached afterwards.
        """
        page = self._home_page
        if page is not None:
            try:
                if page.is_closed():
                    page = None
            except AttributeError:
                pass
        if page is None:
            page = self._require_page()
        current_url = str(getattr(page, "url", ""))
        if not current_url.startswith(KUAISHOU_HOME_SEARCH_URL):
            await page.goto(
                KUAISHOU_HOME_SEARCH_URL,
                wait_until="commit",
                timeout=min(self.settings.navigation_timeout_ms, 12_000),
            )
        current_url = str(getattr(page, "url", ""))
        if not current_url.startswith("https://www.kuaishou.com/"):
            raise RuntimeError(f"Kuaishou home bootstrap did not commit: {current_url}")
        self._home_page = page
        self._page = page
        self._page_url = current_url
        await page.wait_for_timeout(1_000)

    async def _trigger_search_from_home(
        self, keyword: str, probe: NetworkProbe
    ) -> Any:
        """Type in the real home search box and click its Search button.

        Kuaishou applies materially different risk decisions when a fresh page
        is opened directly at ``/search/{keyword}``.  The production path must
        reproduce the site's own home-form transition instead.
        """
        await self._bootstrap_home()
        context = self._require_context()
        home_page = self._home_page
        if home_page is None:
            raise RuntimeError("Kuaishou home page is unavailable")

        # Keep exactly one reusable home tab plus the newly opened result tab.
        for existing_page in list(context.pages):
            if existing_page is home_page:
                continue
            try:
                await existing_page.close()
            except Exception:
                pass
        self._page = home_page
        self._page_url = getattr(home_page, "url", KUAISHOU_HOME_SEARCH_URL)

        search_box = home_page.get_by_role(
            "textbox", name=KUAISHOU_SEARCH_PLACEHOLDER
        )
        search_button = home_page.get_by_role("button", name="搜索", exact=True)
        await search_box.wait_for(
            state="visible", timeout=min(self.settings.navigation_timeout_ms, 10_000)
        )
        await search_box.fill(keyword)
        await probe.mark(
            "home_search_input",
            keyword=keyword,
            home_url=getattr(home_page, "url", None),
        )

        # Keep the result navigation in the already CDP-attached home tab.
        # Kuaishou normally opens a new tab, whose first feed request can race
        # ahead of CDP attachment. This preserves the real search-box action
        # while guaranteeing that the initial /rest/v/search/feed response is
        # observed before logged-in scrolling begins.
        try:
            await home_page.evaluate(
                """
                () => {
                  window.open = (url) => {
                    if (typeof url === 'string' && url) {
                      window.location.assign(url);
                    }
                    return window;
                  };
                  for (const element of document.querySelectorAll(
                    'a[target], form[target]'
                  )) {
                    element.removeAttribute('target');
                  }
                  return true;
                }
                """
            )
        except Exception as exc:
            await probe.mark(
                "same_tab_search_hook_failed",
                keyword=keyword,
                error=f"{type(exc).__name__}: {exc}",
            )

        before_pages = {id(page) for page in context.pages}
        trigger_mode = "search_button"
        try:
            await search_button.click(timeout=5_000)
        except Exception as exc:
            # Search suggestions or a transient transparent mask can briefly
            # intercept pointer events. Filling already focuses the real input;
            # Enter invokes the same home-search form without waiting 30s or
            # treating the overlay as a network/risk-control failure.
            trigger_mode = "input_enter_fallback"
            await probe.mark(
                "home_search_click_blocked",
                keyword=keyword,
                error=f"{type(exc).__name__}: {exc}",
            )
            await search_box.press("Enter")
        await probe.mark(
            "home_search_submit", keyword=keyword, trigger_mode=trigger_mode
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10.0
        search_page = None
        target_url = self.search_url(keyword)
        while loop.time() < deadline:
            candidates = [
                page for page in context.pages if id(page) not in before_pages
            ]
            if candidates:
                search_page = candidates[-1]
                break
            if str(getattr(home_page, "url", "")).startswith(target_url):
                search_page = home_page
                break
            await asyncio.sleep(0.05)
        if search_page is None:
            raise RuntimeError("search button did not open a result page")

        self._page = search_page
        await probe.attach(search_page)
        deadline = loop.time() + 10.0
        while loop.time() < deadline:
            current_url = str(getattr(search_page, "url", ""))
            if current_url.startswith(target_url):
                self._page_url = current_url
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError(
                f"home search did not reach {target_url}: "
                f"{getattr(search_page, 'url', None)}"
            )
        await search_page.wait_for_timeout(1_200)
        return search_page

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
                wait_until="commit",
                timeout=min(self.settings.navigation_timeout_ms, 20_000),
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
            self._probe_generation += 1
            generation = self._probe_generation
            self._probe = None
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
        async with self._lock:
            if (
                generation != self._probe_generation
                or self._probe_state != "starting"
            ):
                cancelled_before_attach = True
            else:
                self._probe = probe
                cancelled_before_attach = False
        if cancelled_before_attach:
            await probe.stop(error="probe start superseded by pause")
            raise ProbeStartCancelled("probe start was cancelled by pause")

        try:
            await self.launch_browser()
            context = self._require_context()
            page = self._require_page()
            await self._bootstrap_home()
            for existing_page in list(context.pages):
                await probe.attach(existing_page)

            await probe.mark(
                "trigger_search_from_home", keyword=keyword, expected_url=target_url
            )
            page = await self._trigger_search_from_home(keyword, probe)
            self._page_url = page.url
            if not str(self._page_url).startswith(target_url):
                raise RuntimeError(
                    f"search page did not commit: expected {target_url}, got {self._page_url}"
                )
            # Keep the destination visible long enough to distinguish a real
            # search navigation from the preceding home/bootstrap transition.
            await page.wait_for_timeout(1_200)
            async with self._lock:
                if (
                    generation != self._probe_generation
                    or self._probe is not probe
                    or not probe.active
                ):
                    raise ProbeStartCancelled(
                        "probe start was cancelled before navigation completed"
                    )
                self._probe_state = "running"
            payload = self.status()
            payload["search_navigation"] = {
                "target_url": target_url,
                "verified": True,
                "mode": "home_search_form",
            }
            return payload
        except ProbeStartCancelled:
            await probe.stop(error="probe start superseded by pause")
            async with self._lock:
                if generation == self._probe_generation:
                    self._probe_state = "stopped"
            raise
        except Exception as exc:
            async with self._lock:
                superseded = generation != self._probe_generation
            if superseded:
                await probe.stop(error="probe start superseded by pause")
                raise ProbeStartCancelled(
                    "probe start was cancelled while navigation was in progress"
                ) from exc
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

    async def direct_comment_count(
        self, video_id: str, timeout_seconds: float = 15.0
    ) -> dict[str, Any]:
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
        result = await asyncio.wait_for(
            page.evaluate(
                """
            async ({ videoId, timeoutMs }) => {
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
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), timeoutMs);
              try {
                const response = await fetch('/graphql', {
                  method: 'POST',
                  credentials: 'include',
                  headers: {
                    'accept': 'application/json',
                    'content-type': 'application/json',
                  },
                  body: JSON.stringify(payload),
                  signal: controller.signal,
                });
                return {
                  ok: response.ok,
                  status: response.status,
                  contentType: response.headers.get('content-type'),
                  interceptResult: response.headers.get('intercept-result'),
                  body: await response.text(),
                  error: null,
                };
              } catch (error) {
                return {
                  ok: false,
                  status: null,
                  contentType: null,
                  interceptResult: null,
                  body: '',
                  error: `${error?.name || 'Error'}: ${error?.message || error}`,
                };
              } finally {
                clearTimeout(timer);
              }
            }
            """,
                {"videoId": video_id, "timeoutMs": int(timeout_seconds * 1000)},
            ),
            timeout=timeout_seconds + 3.0,
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
            "error": result.get("error") if isinstance(result, dict) else None,
            "comment_count": count,
            "response": body,
        }

    async def check_login_status(self) -> dict[str, Any]:
        """Read Kuaishou login state through the page's GraphQL endpoint."""
        page = self._require_page()
        if not str(getattr(page, "url", "")).startswith("https://www.kuaishou.com"):
            raise RuntimeError("open a kuaishou.com page before checking login state")
        result = await page.evaluate(
            """
            async () => {
              const payload = {
                operationName: 'checkLoginQuery',
                variables: {},
                query: `query checkLoginQuery { checkLogin }`,
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
                interceptResult: response.headers.get('intercept-result'),
                body: await response.text(),
              };
            }
            """
        )
        try:
            import json

            body = json.loads(result.get("body", ""))
        except (AttributeError, TypeError, json.JSONDecodeError):
            body = None
        value = (body.get("data") or {}).get("checkLogin") if isinstance(body, dict) else None
        return {
            "logged_in": bool(value),
            "check_login": value,
            "transport_ok": bool(result.get("ok")) if isinstance(result, dict) else False,
            "http_status": result.get("status") if isinstance(result, dict) else None,
            "intercept_result": result.get("interceptResult") if isinstance(result, dict) else None,
        }

    async def repeat_search(self, timeout_seconds: float = 20.0) -> dict[str, Any]:
        """Run one additional search cycle and wait for its feed response."""
        probe = self._probe
        if probe is None or not probe.active:
            raise RuntimeError("start a probe before repeating the search")
        before = probe.status()["extracted"]
        before_responses = (
            before["successful_search_responses"] + before["failed_search_responses"]
        )
        before_unique = before["video_count"]
        target_url = self.search_url(probe.config.keyword)
        await probe.mark(
            "repeat_search",
            keyword=probe.config.keyword,
            response_number=before_responses + 1,
        )
        page = await self._trigger_search_from_home(probe.config.keyword, probe)
        self._page_url = page.url
        if not str(self._page_url).startswith(target_url):
            raise RuntimeError(
                f"repeated search page did not commit: expected {target_url}, got {self._page_url}"
            )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            current = probe.status()["extracted"]
            response_count = (
                current["successful_search_responses"]
                + current["failed_search_responses"]
            )
            if response_count > before_responses:
                return {
                    "keyword": probe.config.keyword,
                    "response_number": response_count,
                    "successful": current["successful_search_responses"]
                    > before["successful_search_responses"],
                    "new_unique_videos": current["video_count"] - before_unique,
                    "video_count": current["video_count"],
                    "search_feed_rows": current["search_feed_rows"],
                    "risk_controls": probe.status()["risk_controls"],
                }
            await asyncio.sleep(0.1)
        raise asyncio.TimeoutError("search feed response timeout")

    async def stop(self) -> dict[str, Any]:
        """Stop only the capture; retain the persistent browser and login."""
        async with self._lock:
            if self._probe_state not in {"running", "error", "starting"}:
                if self._probe is None or not self._probe.active:
                    self._probe_state = "stopped"
                    return self._probe.status() if self._probe is not None else {}
                raise RuntimeError("no active probe session")
            self._probe_generation += 1
            self._probe_state = "stopping"
            probe = self._probe
        summary: dict[str, Any] = {}
        if probe is not None:
            summary = await probe.stop(error=self._last_error)
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
                self._launched_headless = None
                return self.status()
            self._browser_state = "stopping"
            context = self._context
        try:
            await context.close()
        finally:
            self._context = None
            self._page = None
            self._home_page = None
            self._page_url = None
            self._launched_headless = None
            async with self._lock:
                self._browser_state = "stopped"
        return self.status()

    async def reset_identity(self) -> dict[str, Any]:
        """Discard all persisted browser state and launch a fresh identity.

        The sampling checkpoints and captures live outside ``profile_dir`` and
        are deliberately retained.  The removed profile contains cookies,
        HTTP cache, Local/Session Storage, IndexedDB, service workers and other
        site state.  Removing ``identity_file`` makes the next launch generate
        a new CloakBrowser fingerprint seed.
        """
        old_seed = self._fingerprint_seed
        old_identity_id = self._identity_id
        old_temp_dir = self._browser_temp_dir
        await self.close_browser()

        data_dir = self.settings.data_dir.resolve()
        profile_dir = self.settings.profile_dir.resolve()
        if profile_dir == data_dir or data_dir not in profile_dir.parents:
            raise RuntimeError("profile directory must be inside the runtime data directory")

        identity_file = self.settings.identity_file
        browser_temp_root = (
            Path(tempfile.gettempdir()) / "kuaishou-cloakbrowser"
        ).resolve()

        def tree_stats(path: Any) -> dict[str, int]:
            files = 0
            directories = 0
            total_bytes = 0
            if not path.exists():
                return {"files": 0, "directories": 0, "bytes": 0}
            for root, directory_names, file_names in os.walk(path):
                directories += len(directory_names)
                for file_name in file_names:
                    files += 1
                    try:
                        total_bytes += (Path(root) / file_name).stat().st_size
                    except OSError:
                        pass
            return {"files": files, "directories": directories, "bytes": total_bytes}

        removed_profile_stats = tree_stats(profile_dir)
        removed_temp_stats = tree_stats(browser_temp_root)
        reset_token = secrets.token_hex(12)
        quarantine_parent = data_dir / ".identity-reset-trash"
        quarantine = quarantine_parent / reset_token
        quarantine.mkdir(parents=True, exist_ok=False)
        if profile_dir.exists():
            profile_dir.rename(quarantine / "profile")
        if identity_file.exists():
            identity_file.rename(quarantine / "identity.json")
        identity_temp = identity_file.with_suffix(identity_file.suffix + ".tmp")
        if identity_temp.exists():
            identity_temp.rename(quarantine / "identity.json.tmp")
        if browser_temp_root.exists():
            shutil.rmtree(browser_temp_root)
        shutil.rmtree(quarantine)
        try:
            quarantine_parent.rmdir()
        except OSError:
            pass

        if profile_dir.exists() or identity_file.exists() or browser_temp_root.exists():
            raise RuntimeError("deep identity purge verification failed")

        self._probe = None
        self._probe_started_at = None
        self._fingerprint_seed = None
        self._identity_id = None
        self._browser_temp_dir = None
        self._last_error = None

        # Open the known-good home entry using commit-level waiting.  The
        # sampler waits its random interval here, then performs and verifies the
        # actual /search/{keyword} navigation.
        await self.launch_browser()
        await self._bootstrap_home()
        payload = self.status()
        try:
            chromium_version = self._browser_version
        except Exception:
            chromium_version = None
        reset_report = {
            "mode": "deep",
            "reset_token": reset_token,
            "old_identity_id": old_identity_id,
            "new_identity_id": self._identity_id,
            "old_fingerprint_seed": old_seed,
            "new_fingerprint_seed": self._fingerprint_seed,
            "profile_dir": str(profile_dir),
            "identity_file": str(identity_file),
            "old_browser_temp_dir": old_temp_dir,
            "new_browser_temp_dir": self._browser_temp_dir,
            "removed_profile": removed_profile_stats,
            "removed_browser_temp": removed_temp_stats,
            "process_restart_verified": True,
            "old_state_removed_before_relaunch": True,
            "tls_transport": {
                "tls_session_state": "purged",
                "socket_connection_pool": "purged",
                "dns_and_quic_process_state": "purged",
                "tls_clienthello_profile": "stable_browser_build",
                "structural_tls_fingerprint_rotated": False,
                "chromium_version": chromium_version,
                "host_platform": platform.system().lower(),
                "reason": "JA3/JA4 stays coherent with the Chromium build",
            },
            "purged_scopes": [
                "cookies_and_auth_tokens",
                "http_cache_code_cache_gpu_shader_cache",
                "local_storage_session_storage_web_storage",
                "indexeddb_shared_storage_cache_storage",
                "service_workers_background_state",
                "history_sessions_tabs_autofill_password_databases",
                "permissions_site_settings_client_hints",
                "trust_tokens_dips_first_party_sets",
                "hsts_transport_security_network_persistent_state",
                "dns_socket_tls_session_memory_via_process_restart",
                "browser_process_temp_and_xdg_cache",
                "cloak_fingerprint_seed_and_identity_generation",
            ],
            "reset_at": utc_now(),
        }
        payload["identity_reset"] = reset_report
        audit_file = data_dir / "identity-reset-audit.jsonl"
        with audit_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(reset_report, ensure_ascii=False) + "\n")
        return payload

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

    async def search_page_state(self) -> dict[str, Any]:
        """Read only the UI state needed to control search-result scrolling.

        Video records continue to come exclusively from captured network
        responses.  The DOM is used here only as a stop condition, matching the
        visible ``没有更多了`` marker shown by Kuaishou at the result footer.
        """
        page = self._require_page()
        result = await page.evaluate(
            """
            () => {
              const expected = '没有更多了';
              const visible = (element) => {
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && Number(style.opacity || 1) > 0
                  && rect.width > 0
                  && rect.height > 0
                  && rect.bottom >= 0
                  && rect.top <= window.innerHeight;
              };
              const candidates = Array.from(document.querySelectorAll('body *'));
              const marker = candidates.find((element) =>
                (element.textContent || '').trim() === expected && visible(element)
              );
              return {
                noMorePresent: (document.body?.innerText || '').includes(expected),
                noMoreVisible: Boolean(marker),
                markerText: marker ? (marker.textContent || '').trim() : null,
                scrollY: window.scrollY,
                viewportHeight: window.innerHeight,
                documentHeight: Math.max(
                  document.body?.scrollHeight || 0,
                  document.documentElement?.scrollHeight || 0
                ),
              };
            }
            """
        )
        search_page = {
            "no_more_present": bool(result.get("noMorePresent")),
            "no_more_visible": bool(result.get("noMoreVisible")),
            "marker_text": result.get("markerText"),
            "scroll_y": result.get("scrollY"),
            "viewport_height": result.get("viewportHeight"),
            "document_height": result.get("documentHeight"),
        }
        payload = self.status()
        payload["search_page"] = search_page
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
        if self._page_is_closed(self._page):
            raise RuntimeError("browser page is not available")
        return self._page

    def status(self) -> dict[str, Any]:
        page_closed = self._page_is_closed(self._page)
        if self._page is not None and not page_closed:
            self._page_url = getattr(self._page, "url", self._page_url)
        reported_browser_state = self._browser_state
        reported_page_url = self._page_url
        if self._browser_state == "running" and page_closed:
            reported_browser_state = "stopped"
            reported_page_url = None
        reported_probe_state = self._probe_state
        if (
            reported_probe_state in {"starting", "running", "stopping"}
            and (self._probe is None or not self._probe.active)
        ):
            reported_probe_state = "stopped"
        payload: dict[str, Any] = {
            # `state` remains the probe-state alias used by the initial UI/API.
            "state": reported_probe_state,
            "probe_state": reported_probe_state,
            "browser_state": reported_browser_state,
            "started_at": self._probe_started_at,
            "browser_started_at": self._browser_started_at,
            "page_url": reported_page_url,
            "last_error": self._last_error,
            "headless": self.effective_headless(),
            "open_browser_window": not self.effective_headless(),
            "browser_window_open": bool(
                self._context is not None
                and not page_closed
                and self._launched_headless is False
            ),
            "browser_locale": self.settings.browser_locale,
            "browser_timezone": self.settings.browser_timezone,
            "browser_geoip": self.settings.browser_geoip,
            "fingerprint_seed": self._fingerprint_seed,
            "identity_id": self._identity_id,
            "browser_temp_dir": self._browser_temp_dir,
            "browser_version": self._browser_version,
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
