from __future__ import annotations

import asyncio
import json

import pytest

from backend.kuaishou.network_probe import (
    NetworkProbe,
    ProbeConfig,
    parse_post_data,
    redact_headers,
    redact_url,
)


class FakeCdpSession:
    def __init__(self):
        self.listeners = {}
        self.commands = []
        self.bodies = {}
        self.detached = False

    async def send(self, method, params=None):
        self.commands.append((method, params))
        if method == "Network.getResponseBody":
            return self.bodies[params["requestId"]]
        return {}

    def on(self, event, callback):
        self.listeners[event] = callback

    async def detach(self):
        self.detached = True

    def emit(self, event, payload):
        self.listeners[event](payload)


class FakeContext:
    def __init__(self, session):
        self.session = session

    async def new_cdp_session(self, page):
        return self.session


class FakePage:
    def __init__(self, session):
        self.context = FakeContext(session)
        self.url = "about:blank"


async def drain(probe: NetworkProbe):
    for _ in range(10):
        await asyncio.sleep(0)
        pending = list(probe._pending)
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def test_redaction_and_graphql_operation_extraction():
    assert redact_headers({"Authorization": "Bearer x", "accept": "json"}) == {
        "Authorization": "[REDACTED]",
        "accept": "json",
    }
    assert "token=%5BREDACTED%5D" in redact_url(
        "https://example.test/graphql?token=secret&page=2"
    )
    body, operations = parse_post_data(
        json.dumps(
            {
                "operationName": "SearchVideos",
                "variables": {"keyword": "vpn", "accessToken": "secret"},
            }
        ),
        100_000,
    )
    assert operations == ["SearchVideos"]
    assert body["variables"]["accessToken"] == "[REDACTED]"
    assert body["variables"]["keyword"] == "vpn"


@pytest.mark.asyncio
async def test_probe_records_request_response_body_and_summary(tmp_path):
    probe = NetworkProbe(ProbeConfig(root_dir=tmp_path, keyword="vpn"))
    await probe.start()
    session = FakeCdpSession()
    page = FakePage(session)
    await probe.attach(page)

    session.bodies["42"] = {
        "body": json.dumps(
            {
                "data": {
                    "photos": [
                        {
                            "photoId": "abc",
                            "caption": "VPN test",
                            "commentCount": 88,
                        }
                    ],
                    "token": "private-value",
                }
            }
        ),
        "base64Encoded": False,
    }
    session.emit(
        "Network.requestWillBeSent",
        {
            "requestId": "42",
            "type": "Fetch",
            "documentURL": "https://www.kuaishou.com/search/vpn",
            "request": {
                "url": "https://www.kuaishou.com/graphql",
                "method": "POST",
                "headers": {"Cookie": "did=secret", "Content-Type": "application/json"},
                "postData": json.dumps(
                    {"operationName": "SearchVideos", "variables": {"keyword": "vpn"}}
                ),
            },
            "initiator": {"type": "script"},
        },
    )
    session.emit(
        "Network.responseReceived",
        {
            "requestId": "42",
            "type": "Fetch",
            "response": {
                "url": "https://www.kuaishou.com/graphql",
                "status": 200,
                "statusText": "OK",
                "mimeType": "application/json",
                "headers": {"set-cookie": "secret", "content-type": "application/json"},
                "protocol": "h2",
            },
        },
    )
    session.emit(
        "Network.loadingFinished",
        {"requestId": "42", "encodedDataLength": 300},
    )
    await drain(probe)
    summary = await probe.stop()

    assert summary["event_counts"]["request"] == 1
    assert summary["event_counts"]["response"] == 1
    assert summary["event_counts"]["body_saved"] == 1
    assert summary["operation_counts"] == {"SearchVideos": 1}
    assert summary["body_count"] == 1
    assert session.detached is True

    events = [
        json.loads(line)
        for line in probe.events_path.read_text(encoding="utf-8").splitlines()
    ]
    request_event = next(event for event in events if event["type"] == "request")
    assert request_event["headers"]["Cookie"] == "[REDACTED]"
    body_event = next(event for event in events if event["type"] == "body_saved")
    captured = json.loads((probe.session_dir / body_event["file"]).read_text("utf-8"))
    assert captured["data"]["photos"][0]["commentCount"] == 88
    assert captured["data"]["token"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_probe_detects_risk_control_response(tmp_path):
    probe = NetworkProbe(ProbeConfig(root_dir=tmp_path, keyword="vpn"))
    await probe.start()
    session = FakeCdpSession()
    await probe.attach(FakePage(session))
    session.emit(
        "Network.responseReceived",
        {
            "requestId": "risk-1",
            "type": "Fetch",
            "response": {
                "url": "https://www.kuaishou.com/rest/v/search/feed",
                "status": 200,
                "mimeType": "application/json",
                "headers": {"Intercept-Result": "risk-control;2"},
            },
        },
    )
    await drain(probe)
    status = probe.status()
    assert status["event_counts"]["risk_control"] == 1
    assert status["risk_controls"][0]["intercept_result"] == "risk-control;2"
    await probe.stop()


def test_data_urls_are_compacted():
    redacted = redact_url("data:image/png;base64," + "a" * 5000)
    assert redacted.startswith("data:image/png;base64,[DATA_URL_")
    assert len(redacted) < 100


@pytest.mark.asyncio
async def test_probe_records_websocket_frames(tmp_path):
    probe = NetworkProbe(ProbeConfig(root_dir=tmp_path, keyword="网络加速"))
    await probe.start()
    session = FakeCdpSession()
    await probe.attach(FakePage(session))

    session.emit(
        "Network.webSocketCreated",
        {"requestId": "ws-1", "url": "wss://example.test/feed?token=x"},
    )
    session.emit(
        "Network.webSocketFrameReceived",
        {
            "requestId": "ws-1",
            "response": {"opcode": 1, "mask": False, "payloadData": '{"count":88}'},
        },
    )
    await drain(probe)
    summary = await probe.stop()
    assert summary["event_counts"]["websocket_created"] == 1
    assert summary["event_counts"]["websocket_frame"] == 1
