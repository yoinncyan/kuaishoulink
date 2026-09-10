from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

import pytest

from backend.config import Settings
from backend.kuaishou.browser_session import BrowserProbeManager, ProbeStartCancelled


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
        self.closed = False
        self.search_value = ""

    async def goto(self, url, **kwargs):
        self.visited.append((url, kwargs))
        self.url = url

    async def reload(self, **kwargs):
        self.visited.append((self.url, kwargs))

    async def screenshot(self, **kwargs):
        return b"png"

    async def wait_for_timeout(self, milliseconds):
        return None

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True
        if self in self.context.pages:
            self.context.pages.remove(self)

    def get_by_role(self, role, name=None, exact=None):
        return FakeLocator(self, role, name)

    async def evaluate(self, script, arg=None):
        if self.closed:
            raise RuntimeError("page has been closed")
        if "noMoreVisible" in script:
            return {
                "noMorePresent": True,
                "noMoreVisible": True,
                "markerText": "没有更多了",
                "scrollY": 3200,
                "viewportHeight": 800,
                "documentHeight": 4000,
            }
        if "window.open = (url)" in script:
            self.context.force_same_tab = True
            return True
        if "window.innerWidth" in script:
            return {"width": 1000, "height": 800}
        if "checkLoginQuery" in script:
            return {
                "ok": True,
                "status": 200,
                "interceptResult": None,
                "body": '{"data":{"checkLogin":false}}',
            }
        if "checkLoginQuery" in script:
            return {
                "ok": True,
                "status": 200,
                "interceptResult": None,
                "body": '{"data":{"checkLogin":false}}',
            }
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


class FakeLocator:
    def __init__(self, page, role, name):
        self.page = page
        self.role = role
        self.name = name

    async def wait_for(self, **kwargs):
        return None

    async def click(self, **kwargs):
        if self.role != "button":
            return
        if self.page.context.block_button_click:
            raise RuntimeError("overlay intercepts pointer events")
        self._open_result_page()

    def _open_result_page(self):
        if self.page.context.force_same_tab:
            self.page.url = "https://www.kuaishou.com/search/" + quote(
                self.page.search_value, safe=""
            )
            return
        result = FakePage(self.page.context)
        result.url = "https://www.kuaishou.com/search/" + quote(
            self.page.search_value, safe=""
        )
        self.page.context.pages.append(result)
        callback = self.page.context.listeners.get("page")
        if callback:
            callback(result)

    async def fill(self, value):
        self.page.search_value = value

    async def press(self, key):
        if key == "Enter":
            self._open_result_page()


class FakeContext:
    def __init__(self):
        self.listeners = {}
        self.session = FakeCdpSession()
        self.pages = []
        self.closed = False
        self.block_button_click = False
        self.force_same_tab = False
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


def test_automated_browser_is_headless_by_default():
    with patch.dict("os.environ", {}, clear=True):
        assert Settings.from_env().headless is True


def test_visible_browser_remains_an_explicit_debug_option():
    with patch.dict(
        "os.environ", {"KUAISHOU_BROWSER_HEADLESS": "false"}, clear=True
    ):
        assert Settings.from_env().headless is False


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
    assert status["search_navigation"]["verified"] is True
    assert launch_options["user_data_dir"] == tmp_path / "profiles" / "kuaishou"
    assert launch_options["headless"] is True
    home_page = context.pages[0]
    assert [visit[0] for visit in home_page.visited] == [
        "https://www.kuaishou.com/?isHome=1&source=SEARCH"
    ]
    assert home_page.search_value == "vpn"
    assert status["search_navigation"]["mode"] == "home_search_form"
    assert "Network.requestWillBeSent" in context.session.listeners

    summary = await manager.stop()
    assert context.closed is False
    assert manager.status()["state"] == "stopped"
    assert summary["event_counts"]["action"] >= 2
    await manager.close_browser()
    assert context.closed is True


@pytest.mark.asyncio
async def test_home_search_uses_enter_when_overlay_blocks_button(tmp_path):
    context = FakeContext()
    context.block_button_click = True

    async def launcher(**kwargs):
        return context

    manager = BrowserProbeManager(settings(tmp_path), launch_context=launcher)
    status = await manager.start("vpn")

    assert status["page_url"] == "https://www.kuaishou.com/search/vpn"
    assert status["search_navigation"]["mode"] == "home_search_form"
    assert any(
        event.get("action") == "home_search_click_blocked"
        for event in status["probe"]["recent_events"]
    )
    await manager.stop()
    await manager.close_browser()


@pytest.mark.asyncio
async def test_window_mode_can_switch_next_launch_from_headless_to_visible(tmp_path):
    contexts = []
    launches = []

    async def launcher(**kwargs):
        launches.append(kwargs)
        context = FakeContext()
        contexts.append(context)
        return context

    manager = BrowserProbeManager(settings(tmp_path), launch_context=launcher)
    await manager.launch_browser()
    assert launches[0]["headless"] is True

    status = await manager.configure_window_mode(True)
    assert contexts[0].closed is True
    assert status["headless"] is False
    assert status["open_browser_window"] is True

    await manager.launch_browser()
    assert launches[1]["headless"] is False
    assert manager.status()["browser_window_open"] is True
    await manager.close_browser()


@pytest.mark.asyncio
async def test_closed_visible_page_is_reported_stopped_and_relaunched(tmp_path):
    contexts = []
    launches = []

    async def launcher(**kwargs):
        launches.append(kwargs)
        context = FakeContext()
        contexts.append(context)
        return context

    manager = BrowserProbeManager(settings(tmp_path), launch_context=launcher)
    await manager.navigate("https://www.kuaishou.com/?isHome=1&source=SEARCH")
    identity_id = manager.status()["identity_id"]
    contexts[0].pages[0].closed = True

    status = manager.status()
    assert status["browser_state"] == "stopped"
    assert status["page_url"] is None

    recovered = await manager.navigate(
        "https://www.kuaishou.com/?isHome=1&source=SEARCH"
    )
    assert len(launches) == 2
    assert contexts[0].closed is True
    assert recovered["browser_state"] == "running"
    assert recovered["identity_id"] == identity_id
    await manager.close_browser()


@pytest.mark.asyncio
async def test_pause_during_probe_start_cannot_restore_stale_running_state(tmp_path):
    context = FakeContext()
    navigation_started = asyncio.Event()
    release_navigation = asyncio.Event()

    async def launcher(**kwargs):
        return context

    manager = BrowserProbeManager(settings(tmp_path), launch_context=launcher)
    original_trigger = manager._trigger_search_from_home

    async def delayed_trigger(keyword, probe):
        navigation_started.set()
        await release_navigation.wait()
        return await original_trigger(keyword, probe)

    manager._trigger_search_from_home = delayed_trigger
    start_task = asyncio.create_task(manager.start("vpn"))
    await navigation_started.wait()

    await manager.stop()
    release_navigation.set()

    with pytest.raises(ProbeStartCancelled):
        await start_task
    status = manager.status()
    assert status["probe_state"] == "stopped"
    assert status["probe"]["active"] is False
    await manager.close_browser()


@pytest.mark.asyncio
async def test_profile_switch_restarts_browser_with_isolated_identity(tmp_path):
    contexts = []
    launches = []

    async def launcher(**kwargs):
        launches.append(kwargs)
        context = FakeContext()
        contexts.append(context)
        return context

    manager = BrowserProbeManager(settings(tmp_path), launch_context=launcher)
    await manager.open_login()
    original = manager.status()
    original_profile = Path(original["profile_dir"])
    (original_profile / "Default" / "Cookies").parent.mkdir(parents=True)
    (original_profile / "Default" / "Cookies").write_text("primary")

    created = await manager.create_profile("备用账号", activate=True)
    secondary = created["created"]
    secondary_status = created["browser"]

    assert contexts[0].closed is True
    assert secondary_status["profile_id"] == secondary["profile_id"]
    assert secondary_status["fingerprint_seed"] != original["fingerprint_seed"]
    assert Path(secondary_status["profile_dir"]) != original_profile
    assert launches[1]["user_data_dir"] == Path(secondary_status["profile_dir"])
    assert (original_profile / "Default" / "Cookies").read_text() == "primary"

    restored = await manager.activate_profile("kuaishou")
    assert contexts[1].closed is True
    assert restored["profile_id"] == "kuaishou"
    assert restored["fingerprint_seed"] == original["fingerprint_seed"]
    assert launches[2]["user_data_dir"] == original_profile
    await manager.close_browser()


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
    login = await manager.check_login_status()
    assert login["logged_in"] is False
    login = await manager.check_login_status()
    assert login["logged_in"] is False
    scroll = await manager.scroll_page(2000)
    assert scroll["scroll"]["target"] == "right-content-pane"
    assert context.pages[0].mouse.moves == [(720.0, 576.0)]
    assert context.pages[0].mouse.wheels == [(0, 2000 / 3)] * 3
    page_state = await manager.search_page_state()
    assert page_state["search_page"]["no_more_visible"] is True
    assert page_state["search_page"]["marker_text"] == "没有更多了"
    await manager.reload_page()
    await manager.close_browser()
    assert context.closed is True


@pytest.mark.asyncio
async def test_reset_identity_removes_profile_and_generates_new_seed(tmp_path):
    contexts = []
    launch_options = []

    async def launcher(**kwargs):
        context = FakeContext()
        contexts.append(context)
        launch_options.append(kwargs)
        return context

    configured = settings(tmp_path)
    manager = BrowserProbeManager(configured, launch_context=launcher)
    secondary = manager.profiles.create("备用账号")
    secondary_dir = manager.profiles.profile_dir(secondary["profile_id"])
    (secondary_dir / "Cache").mkdir()
    (secondary_dir / "Cache" / "entry").write_text("secondary")
    await manager.open_login()
    old_seed = manager.status()["fingerprint_seed"]
    old_identity_id = manager.status()["identity_id"]
    old_temp_dir = Path(manager.status()["browser_temp_dir"])
    (old_temp_dir / "socket-cache").write_text("old-process-state")
    (configured.profile_dir / "Default" / "Cache").mkdir(parents=True)
    (configured.profile_dir / "Default" / "Cache" / "entry").write_text("cached")
    identity_file = manager.profiles.identity_file("kuaishou")
    assert identity_file.exists()

    status = await manager.reset_identity()

    assert contexts[0].closed is True
    assert len(contexts) == 2
    assert status["browser_state"] == "running"
    assert status["page_url"] == (
        "https://www.kuaishou.com/?isHome=1&source=SEARCH"
    )
    assert status["identity_reset"]["old_fingerprint_seed"] == old_seed
    assert status["identity_reset"]["new_fingerprint_seed"] != old_seed
    assert status["identity_reset"]["mode"] == "deep"
    assert status["identity_reset"]["old_identity_id"] == old_identity_id
    assert status["identity_reset"]["new_identity_id"] != old_identity_id
    assert status["identity_reset"]["process_restart_verified"] is True
    assert status["identity_reset"]["tls_transport"]["tls_session_state"] == "purged"
    assert (
        status["identity_reset"]["tls_transport"]["tls_clienthello_profile"]
        == "stable_browser_build"
    )
    assert "cookies_and_auth_tokens" in status["identity_reset"]["purged_scopes"]
    assert not (configured.profile_dir / "Default" / "Cache" / "entry").exists()
    assert not old_temp_dir.exists()
    assert identity_file.exists()
    assert (secondary_dir / "Cache" / "entry").read_text() == "secondary"
    assert manager.profiles.get(secondary["profile_id"])["identity_id"] == (
        secondary["identity_id"]
    )
    assert (configured.profile_dir / ".cloak-identity.json").exists()
    assert (configured.data_dir / "identity-reset-audit.jsonl").exists()
    assert launch_options[1]["args"][-1] == (
        f"--fingerprint={status['identity_reset']['new_fingerprint_seed']}"
    )
