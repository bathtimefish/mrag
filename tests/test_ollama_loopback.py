"""An Ollama endpoint named `localhost` is tried at 127.0.0.1 first.

Windows resolves `localhost` to ::1 first, Ollama on Windows listens on
127.0.0.1, and Windows takes about 2 s to refuse each connection to ::1. These
tests pin the order, the fallback when 127.0.0.1 refuses, that the address that
answered is asked first afterwards, and that nothing else changes.
"""
from unittest.mock import patch

import httpx
import pytest

from mrag.core.ollama_client import (
    model_capabilities,
    ollama_post,
    probe_connection,
    reset_capability_cache,
    send_preferring_ipv4_loopback,
)


def _ok(url: str, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=payload or {"ok": True}, request=httpx.Request("POST", url))


class _Server:
    """Answers at the URLs in `listening`, refuses every other one."""

    def __init__(self, *listening: str) -> None:
        self.listening = listening
        self.seen: list[str] = []

    def __call__(self, url: str, **kwargs) -> httpx.Response:
        self.seen.append(url)
        if not url.startswith(self.listening):
            raise httpx.ConnectError("refused")
        return _ok(url)


def test_localhost_is_asked_at_127_0_0_1_first():
    server = _Server("http://127.0.0.1:11434")
    with patch("httpx.post", side_effect=server):
        ollama_post("http://localhost:11434", "/api/embed", {"model": "m", "input": ["x"]})
    assert server.seen == ["http://127.0.0.1:11434/api/embed"]


@pytest.mark.parametrize(
    "endpoint",
    ["http://127.0.0.1:11434", "http://[::1]:11434", "http://ollama.internal:11434", "https://example.com"],
)
def test_other_hosts_are_asked_as_written(endpoint):
    server = _Server(endpoint)
    with patch("httpx.post", side_effect=server):
        ollama_post(endpoint, "/api/embed", {})
    assert server.seen == [f"{endpoint}/api/embed"]


def test_an_ollama_on_ipv6_only_is_still_reached_and_asked_first_afterwards():
    server = _Server("http://localhost:11434")
    with patch("httpx.post", side_effect=server):
        ollama_post("http://localhost:11434", "/api/embed", {})
        ollama_post("http://localhost:11434", "/api/embed", {})
    assert server.seen == [
        "http://127.0.0.1:11434/api/embed",
        "http://localhost:11434/api/embed",
        # The refused IPv4 attempt is paid once per process, not per call.
        "http://localhost:11434/api/embed",
    ]


def test_the_remembered_address_still_falls_back_when_it_stops_answering():
    ipv6_only = _Server("http://localhost:11434")
    ipv4_only = _Server("http://127.0.0.1:11434")
    with patch("httpx.post", side_effect=ipv6_only):
        ollama_post("http://localhost:11434", "/api/embed", {})
    with patch("httpx.post", side_effect=ipv4_only):
        ollama_post("http://localhost:11434", "/api/embed", {})
    assert ipv4_only.seen == ["http://localhost:11434/api/embed", "http://127.0.0.1:11434/api/embed"]


def test_both_refused_is_a_connection_error_naming_the_configured_endpoint():
    server = _Server()
    with patch("httpx.post", side_effect=server), pytest.raises(ConnectionError) as raised:
        ollama_post("http://localhost:11434", "/api/embed", {}, max_attempts=3)
    assert "http://localhost:11434" in str(raised.value)
    # A refusal is not retried, so each address is tried once.
    assert server.seen == ["http://127.0.0.1:11434/api/embed", "http://localhost:11434/api/embed"]


def test_a_failure_other_than_a_refusal_does_not_switch_address():
    seen = []

    def timing_out(url, **kwargs):
        seen.append(url)
        raise httpx.ReadTimeout("slow")

    with patch("httpx.post", side_effect=timing_out), patch("time.sleep"), pytest.raises(RuntimeError):
        ollama_post("http://localhost:11434", "/api/generate", {}, max_attempts=2)
    assert seen == ["http://127.0.0.1:11434/api/generate"] * 2


def test_port_path_case_and_userinfo_are_kept():
    server = _Server("http://u:p@127.0.0.1:8080/base")
    with patch("httpx.post", side_effect=server):
        send_preferring_ipv4_loopback(httpx.post, "http://u:p@LocalHost:8080/base/api/embed", json={})
    assert server.seen == ["http://u:p@127.0.0.1:8080/base/api/embed"]


def test_no_port_is_kept_as_no_port():
    server = _Server("http://127.0.0.1/")
    with patch("httpx.post", side_effect=server):
        send_preferring_ipv4_loopback(httpx.post, "http://localhost/api/embed", json={})
    assert server.seen == ["http://127.0.0.1/api/embed"]


def test_the_request_body_and_options_reach_the_server_unchanged():
    captured = {}

    def server(url, **kwargs):
        captured.update(kwargs)
        return _ok(url)

    with patch("httpx.post", side_effect=server):
        ollama_post("http://localhost:11434", "/api/embed", {"model": "m", "input": ["x"]}, timeout=7.0)
    assert captured == {"json": {"model": "m", "input": ["x"]}, "timeout": 7.0}


def test_the_probe_and_the_capability_lookup_prefer_ipv4_too():
    reset_capability_cache()
    posts = _Server("http://127.0.0.1:11434")
    gets = _Server("http://127.0.0.1:11434")
    with patch("httpx.post", side_effect=posts), patch("httpx.get", side_effect=gets):
        model_capabilities("http://localhost:11434", "m")
        probe_connection("http://localhost:11434")
    reset_capability_cache()
    assert posts.seen == ["http://127.0.0.1:11434/api/show"]
    assert gets.seen == ["http://127.0.0.1:11434/api/version"]


def test_doctor_asks_the_default_endpoint_at_127_0_0_1_first(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from mrag.cli import app

    monkeypatch.chdir(tmp_path)
    seen = []

    def server(url, **kwargs):
        seen.append(url)
        return httpx.Response(200, json={"version": "0.40.15"}, request=httpx.Request("GET", url))

    with patch("httpx.get", side_effect=server):
        result = CliRunner().invoke(app, ["doctor"], catch_exceptions=False)
    assert seen and all(url == "http://127.0.0.1:11434/api/version" for url in seen)
    # The report still names the endpoint a profile would write.
    assert "running at http://localhost:11434" in " ".join(result.output.split())
