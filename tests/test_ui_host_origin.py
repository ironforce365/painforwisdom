"""The console trusts only its own address: no DNS rebinding through Host, no cross-site POST through Origin."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline.ui import actions, publish
from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings

pytestmark = pytest.mark.integration

SELF = {"Host": "127.0.0.1:8820"}


@pytest.fixture
def client(tmp_path):
    (tmp_path / "inbox").mkdir()
    return TestClient(create_app(Settings(processed_root=tmp_path, inbox=tmp_path / "inbox")), base_url="http://127.0.0.1")


@pytest.fixture
def called(monkeypatch):
    """Which action a request reached: refused ones must reach none."""
    seen: list[str] = []
    monkeypatch.setattr(actions, "stop", lambda *a: seen.append("stop"))
    monkeypatch.setattr(actions, "retry", lambda *a: seen.append("retry"))
    monkeypatch.setattr(publish, "confirm", lambda *a: seen.append("publish") or {"status": "publishing"})
    return seen


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8820", "localhost", "localhost:8820", "[::1]", "[::1]:8820",
                                  "LOCALHOST:8820"])
def test_the_local_hosts_are_served(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8820", "localhost.evil.example", "127.0.0.1.evil.example",
                                  "evil.example:127.0.0.1", "127.0.0.1@evil.example", "[::2]:8820", "[::1]evil", ""])
@pytest.mark.parametrize("path", ["/health", "/api/runs", "/", "/static/index.html", "/nothing/here"])
def test_any_other_host_reads_nothing(client, host, path):
    resp = client.get(path, headers={"Host": host})
    assert resp.status_code == 403
    assert "host" in resp.json()["detail"].lower()


def test_any_other_host_posts_nothing(client, called):
    resp = client.post("/api/runs/r/stop", headers={"Host": "evil.example"})
    assert resp.status_code == 403 and "detail" in resp.json() and called == []
    resp = client.post("/api/runs/r/publish/youtube", headers={"Host": "evil.example", "Content-Type": "application/json"},
                       json={"fields": {}})
    assert resp.status_code == 403 and called == []


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:9000", "http://localhost:8820", "null",
                                    "http://127.0.0.1:8820.evil.example", "http://[bad"])
def test_a_bare_post_from_another_origin_is_refused(client, called, origin):
    resp = client.post("/api/runs/r/stop", headers={**SELF, "Origin": origin})
    assert resp.status_code == 403 and "origin" in resp.json()["detail"].lower()
    assert called == []


@pytest.mark.parametrize("path, action", [("/api/runs/r/retry", "retry"), ("/api/runs/r/publish/youtube", "publish")])
def test_a_json_post_from_another_origin_is_refused(client, called, path, action):
    body = {"fields": {}} if action == "publish" else {"auto_approve": True}
    resp = client.post(path, headers={**SELF, "Origin": "https://evil.example"}, json=body)
    assert resp.status_code == 403 and "origin" in resp.json()["detail"].lower()
    assert called == []


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_other_writes_from_another_origin_are_refused(client, method):
    resp = client.request(method, "/api/runs/r/artifacts/a", headers={**SELF, "Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_a_post_from_the_console_itself_reaches_the_action(client, called):
    resp = client.post("/api/runs/r/stop", headers={**SELF, "Origin": "http://127.0.0.1:8820"})
    assert resp.status_code == 202 and called == ["stop"]
    resp = client.post("/api/runs/r/retry", headers={**SELF, "Origin": "http://127.0.0.1:8820"}, json={"auto_approve": False})
    assert resp.status_code == 202 and called == ["stop", "retry"]


def test_a_post_with_no_origin_is_a_local_client(client, called):
    assert client.post("/api/runs/r/stop", headers=SELF).status_code == 202 and called == ["stop"]


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_reads_do_not_check_origin(client, method):
    resp = client.request(method, "/health", headers={**SELF, "Origin": "https://evil.example"})
    assert resp.status_code != 403
