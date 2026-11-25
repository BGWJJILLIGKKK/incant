"""Shared test setup."""

from ipaddress import ip_address

import pytest


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch):
    """URL ingest resolves every host to refuse private addresses. Tests never hit the network: a literal IP or
    localhost resolves to itself, any other name to a public documentation-range stand-in (TEST-NET-3 is
    reserved, so 93.184.216.34, a public address, is used)."""
    from incant.stages.ingest.url import url as url_mod

    def resolve(host: str) -> list[str]:
        if host in ("localhost", "localhost.localdomain"):
            return ["127.0.0.1", "::1"]
        try:
            return [str(ip_address(host))]
        except ValueError:
            return ["93.184.216.34"]

    monkeypatch.setattr(url_mod, "_resolve", resolve)
