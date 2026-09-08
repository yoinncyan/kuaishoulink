from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import Settings
from backend.kuaishou.browser_session import BrowserProbeManager


class FakeCdpSession:
    def __init__(self):
        self.listeners = {}

    async def send(self, method, params=None):
        return {}

    def on(self, event, callback):
        self.listeners[event] = callback

    async def detach(self):
        return None


class FakePage:
    def __init__(self, context):
        self.context = context
        self.url = "about:blank"
        self.visited = []
        self.mouse = FakeMouse()

    async def goto(self, url, **kwargs):
        self.visited.append((url, kwargs))
        self.url = url

    async def reload(self, **kwargs):
        self.visited.append((self.url, kwargs))

    async def screenshot(self, **kwargs):
        return b"png"

    async def wait_for_timeout(self, milliseconds):
        return None

    async def evaluate(self, script, arg=None):
        if "window.innerWidth" in script:
            return {"width": 1000, "height": 800}
        return {
            "ok": True,
            "status": 200,
            "contentType": "application/json",
            "interceptResult": None,
            "body": '{"data":{"visionCommentList":{"commentCount":null,"commentCountV2":745}}}',
        }


class FakeMouse:
    def __init__(self):
        self.wheels = []
        self.moves = []

    async def move(self, x, y):
        self.moves.append((x, y))

    async def wheel(self, delta_x, delta_y):
        self.wheels.append((delta_x, delta_y))


class FakeContext:
    def __init__(self):
        self.listeners = {}
        self.session = FakeCdpSession()
        self.pages = []
        self.closed = False
        self.pages.append(FakePage(self))

    def on(self, event, callback):
        self.listeners[event] = callback

    async def new_page(self):
        page = FakePage(self)
        self.pages.append(page)
        return page

    async def new_cdp_session(self, page):
        return self.session

    async def close(self):
        self.closed = True


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        profile_dir=tmp_path / "profiles" / "kuaishou",
        captures_dir=tmp_path / "captures",
        headless=True,
        browser_locale="zh-CN",
        browser_timezone="Asia/Shanghai",
        browser_geoip=False,
        browser_width=1440,
        browser_height=900,
        max_body_bytes=1024 * 1024,
        max_post_data_bytes=1024 * 1024,
        navigation_timeout_ms=10_000,
        license_key=None,
        release_channel=None,
        proxy=None,
    )


def test_search_url_is_trimmed_and_encoded(tmp_path):
    manager = BrowserProbeManager(settings(tmp_path))
    assert manager.search_url(" vpn ") == "https://www.kuaishou.com/search/vpn"
    assert manager.search_url("网络 加速") == (
        "https://www.kuaishou.com/search/%E7%BD%91%E7%BB%9C%20%E5%8A%A0%E9%80%9F"
    )
    with pytest.raises(ValueError):
        manager.search_url("   ")


@pytest.mark.asyncio
async def test_manager_launches_persistent_profile_and_navigates(tmp_path):
    context = FakeContext()
    launch_options = {}

    async def launcher(**kwargs):
        launch_options.update(kwargs)
        return context

    manager = BrowserProbeManager(settings(tmp_path), launch_context=launcher)
    status = await manager.start("vpn")

    assert status["state"] == "running"
    assert status["browser_state"] == "running"
    assert status["page_url"] == "https://www.kuaishou.com/search/vpn"
    assert launch_options["user_data_dir"] == tmp_path / "profiles" / "kuaishou"
    assert launch_options["headless"] is True
    assert context.pages[0].visited[0][0] == "https://www.kuaishou.com/search/vpn"
    assert "Network.requestWillBeSent" in context.session.listeners

    summary = await manager.stop()
    assert context.closed is False
    assert manager.status()["state"] == "stopped"
    assert summary["event_counts"]["action"] >= 2
    await manager.close_browser()
    assert context.closed is True


@pytest.mark.asyncio
async def test_login_browser_lifetime_is_independent_from_probe(tmp_path):
    context = FakeContext()

    async def launcher(**kwargs):
        return context

    manager = BrowserProbeManager(settings(tmp_path), launch_context=launcher)
    status = await manager.open_login()
    assert status["browser_state"] == "running"
    assert status["probe_state"] == "stopped"
    assert context.pages[0].url == "https://www.kuaishou.com/"
    status = await manager.navigate("https://www.kuaishou.com/?isHome=1&source=SEARCH")
    assert status["page_url"] == "https://www.kuaishou.com/?isHome=1&source=SEARCH"
    with pytest.raises(ValueError):
        await manager.navigate("https://example.com/")
    assert await manager.screenshot() == b"png"
    direct = await manager.direct_comment_count("3x9su263zefax89")
    assert direct["comment_count"] == 745
    assert direct["transport_ok"] is True
    scroll = await manager.scroll_page(2000)
    assert scroll["scroll"]["target"] == "right-content-pane"
    assert context.pages[0].mouse.moves == [(720.0, 576.0)]
    assert context.pages[0].mouse.wheels == [(0, 2000 / 3)] * 3
    await manager.reload_page()
    await manager.close_browser()
    assert context.closed is True
