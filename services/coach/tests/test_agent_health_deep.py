"""GET /health/deep — deep health from *inside* the agent container.

The 2026-07-01→04 outage: the container's pinned DNS upstream died, so the
agent couldn't resolve api.anthropic.com while the shallow /health kept
returning ok. /health/deep runs the checks that actually predict a working
turn — DNS resolution of the API host and mem0 reachability — and returns 503
when any of them fail, so the host watchdog can heal on effect, not guesswork.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import agent.service as svc


@pytest.fixture
def client():
    return TestClient(svc.app, raise_server_exceptions=False)


def test_deep_health_ok_when_all_checks_pass(client, monkeypatch):
    monkeypatch.setattr(svc, "_check_api_dns", lambda: True)
    monkeypatch.setattr(svc, "_check_mem0", lambda: True)
    r = client.get("/health/deep")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"api_dns": True, "mem0": True}


def test_deep_health_503_when_dns_fails(client, monkeypatch):
    monkeypatch.setattr(svc, "_check_api_dns", lambda: False)
    monkeypatch.setattr(svc, "_check_mem0", lambda: True)
    r = client.get("/health/deep")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["api_dns"] is False


def test_deep_health_503_when_mem0_fails(client, monkeypatch):
    monkeypatch.setattr(svc, "_check_api_dns", lambda: True)
    monkeypatch.setattr(svc, "_check_mem0", lambda: False)
    r = client.get("/health/deep")
    assert r.status_code == 503
    assert r.json()["checks"]["mem0"] is False


def test_check_helpers_never_raise(monkeypatch):
    # The checks wrap network calls; any exception must read as False, never
    # bubble into the endpoint.
    import socket

    def boom(*a, **kw):
        raise socket.gaierror(-3, "Temporary failure in name resolution")

    monkeypatch.setattr(svc.socket, "getaddrinfo", boom)
    assert svc._check_api_dns() is False
