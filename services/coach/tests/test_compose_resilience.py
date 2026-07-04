"""docker-compose.yml resilience contract (2026-07-04 outage).

Docker captures the host's DNS upstreams into a container's embedded resolver
at container-START time only. The stack started while Tailscale MagicDNS was
the host resolver; the host later moved on and every coach container silently
lost external DNS for 3 days. Pinning public resolvers in compose removes the
dependency on host-resolver state entirely.

Also pins: HF offline mode for the agent (the reranker must never need
huggingface.co at runtime — model bytes live on the cache volume) and shallow
liveness healthchecks so `docker ps` shows component health at a glance.
"""
from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.yml"

# Long-running coach services; the one-shot eval profile is exempt from
# healthchecks but still gets DNS pins.
LONG_RUNNING = ("mem0-postgres", "mem0-api", "coach-agent", "telegram-bot", "monitor")


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def test_every_service_pins_public_dns():
    for name, svc in _services().items():
        dns = svc.get("dns")
        assert dns, f"service {name} has no dns: pin — container DNS would depend on host-resolver state at start time"
        assert len(dns) >= 2, f"service {name} needs ≥2 resolvers for redundancy"


def test_agent_runs_hf_offline():
    env = _services()["coach-agent"]["environment"]
    assert "HF_HUB_OFFLINE" in env, "reranker must not need huggingface.co at runtime"


def test_agent_and_mem0_have_healthchecks():
    services = _services()
    for name in ("coach-agent", "mem0-api", "monitor"):
        assert "healthcheck" in services[name], f"{name} needs a liveness healthcheck"
