"""The AI harness runs offline: a socket or name lookup for anything but loopback or a Unix
socket (the test database) fails the test instead of leaving the machine. Canary provider
keys and a session secret are set for every test, so each request-body check proves they
never travel (the providers are fakes; nothing reads them for a call)."""

from __future__ import annotations

import socket

import pytest

# The scenario fixtures of the existing agent suites (one seeded graph each).
from test_agent_classes import ac  # noqa: F401
from test_ai_gateway import gw  # noqa: F401
from test_auto_train_decision_points import hk  # noqa: F401
from test_decision_record_service import g, setup  # noqa: F401
from test_lead_agent import lead  # noqa: F401

LOOPBACK = ("127.0.0.1", "::1", "localhost")
CANARY_SECRETS = {"DCLAB_OPENAI_API_KEY": "sk-canary-openai-0000000000000000",
                  "DCLAB_TYPESAFE_API_KEY": "ts-canary-typesafe-key-000000",
                  "DCLAB_CANARY_SESSION_SECRET": "canary-session-secret-000000"}


class OfflineViolation(AssertionError):
    pass


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    real_connect, real_connect_ex, real_lookup = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def check(sock, address):
        if sock.family != getattr(socket, "AF_UNIX", None) and not (
                isinstance(address, tuple) and str(address[0]) in LOOPBACK):
            raise OfflineViolation(f"network access to {address!r} in the offline AI harness")

    def lookup(host, *args, **kwargs):
        if host not in (None, *LOOPBACK):
            raise OfflineViolation(f"name lookup of {host!r} in the offline AI harness")
        return real_lookup(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", lambda sock, address: check(sock, address) or real_connect(
        sock, address))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda sock, address: check(sock, address) or real_connect_ex(
        sock, address))
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    for name, value in CANARY_SECRETS.items():
        monkeypatch.setenv(name, value)
