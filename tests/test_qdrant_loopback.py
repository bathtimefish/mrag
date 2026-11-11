"""A Qdrant server named `localhost` is tried at 127.0.0.1 first.

Windows resolves `localhost` to ::1 first, a Qdrant published on 127.0.0.1
alone does not answer there, and Windows takes about 2 s to refuse each
connection to ::1. These tests pin the order, the fallback when 127.0.0.1
refuses, that the address that answered is asked first afterwards, that no
client is built for an address that refused, and that nothing else changes.
"""
from unittest.mock import patch

import httpx
import pytest
from qdrant_client.http.exceptions import ResponseHandlingException

from mrag.db.qdrant import make_client


class _Server:
    """Accepts connections on the hosts in `listening` and refuses the rest.

    Stands in for both the connection test and QdrantClient. A client built
    for a refusing host fails get_collections the way qdrant-client does, with
    an httpx.ConnectError wrapped in ResponseHandlingException.
    """

    def __init__(self, *listening: str, failure: Exception | None = None) -> None:
        self.listening = listening
        self.failure = failure
        self.probed: list[tuple[str, int]] = []
        self.built: list[tuple[str, int]] = []

    def accepts(self, host: str, port: int) -> bool:
        self.probed.append((host, port))
        return host in self.listening

    def client(self, host: str, port: int, **kwargs):
        self.built.append((host, port))
        server = self

        class _Client:
            def get_collections(self):
                if server.failure is not None:
                    raise server.failure
                if host not in server.listening:
                    raise ResponseHandlingException(httpx.ConnectError("refused"))
                return []

        built = _Client()
        built.host = host
        return built


def _client(server: _Server, **kwargs):
    with patch("mrag.db.qdrant._accepts", side_effect=server.accepts), \
            patch("mrag.db.qdrant.QdrantClient", side_effect=server.client):
        return make_client(**kwargs)


def test_localhost_is_asked_at_127_0_0_1_first():
    server = _Server("127.0.0.1")
    client = _client(server, host="localhost", port=6333)
    assert server.probed == [("127.0.0.1", 6333)]
    assert server.built == [("127.0.0.1", 6333)]
    # The client every later call goes through is the one that answered.
    assert client.host == "127.0.0.1"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "qdrant.internal", "example.com"])
def test_other_hosts_are_asked_as_written_without_a_connection_test(host):
    server = _Server(host)
    _client(server, host=host, port=6333)
    assert server.probed == []
    assert server.built == [(host, 6333)]


def test_a_qdrant_on_ipv6_only_is_still_reached_and_asked_first_afterwards():
    server = _Server("localhost")
    first = _client(server, host="localhost", port=6333)
    second = _client(server, host="localhost", port=6333)
    assert first.host == second.host == "localhost"
    # The refused IPv4 test is paid once per process, not per command.
    assert server.probed == [("127.0.0.1", 6333), ("localhost", 6333)]
    assert server.built == [("localhost", 6333), ("localhost", 6333)]


def test_a_refused_address_gets_no_client():
    # A client is built only where it will be used: building one starts
    # qdrant-client's version check, which warns when it cannot connect.
    server = _Server("localhost")
    _client(server, host="localhost", port=6333)
    assert ("127.0.0.1", 6333) not in server.built


def test_the_remembered_address_still_falls_back_when_it_stops_answering():
    _client(_Server("localhost"), host="localhost", port=6333)
    ipv4_only = _Server("127.0.0.1")
    client = _client(ipv4_only, host="localhost", port=6333)
    assert ipv4_only.probed == [("localhost", 6333)]
    assert client.host == "127.0.0.1"


def test_the_choice_is_remembered_per_port():
    _client(_Server("localhost"), host="localhost", port=6333)
    other = _Server("127.0.0.1")
    _client(other, host="localhost", port=7333)
    assert other.probed == [("127.0.0.1", 7333)]


def test_both_refused_is_a_connection_error_naming_the_configured_host():
    server = _Server()
    with pytest.raises(ConnectionError) as raised:
        _client(server, host="localhost", port=6333)
    assert "Cannot connect to Qdrant at localhost:6333" in str(raised.value)
    assert server.built == [("localhost", 6333)]


def test_a_failure_after_the_connection_is_accepted_does_not_switch_address():
    server = _Server("127.0.0.1", failure=ResponseHandlingException(httpx.ReadTimeout("slow")))
    with pytest.raises(ConnectionError) as raised:
        _client(server, host="localhost", port=6333)
    assert "localhost:6333" in str(raised.value)
    assert server.built == [("127.0.0.1", 6333)]


def test_the_host_is_matched_without_regard_to_case():
    server = _Server("127.0.0.1")
    _client(server, host="LocalHost", port=6333)
    assert server.built == [("127.0.0.1", 6333)]


def test_local_mode_opens_the_path_and_asks_no_host(tmp_path):
    server = _Server()
    opened = []

    def local(**kwargs):
        opened.append(kwargs)
        return object()

    with patch("mrag.db.qdrant._accepts", side_effect=server.accepts), \
            patch("mrag.db.qdrant.QdrantClient", side_effect=local):
        make_client(mode="local", host="localhost", path=tmp_path / "qdrant")
    assert opened == [{"path": str(tmp_path / "qdrant")}]
    assert server.probed == []


def test_the_connection_test_is_a_plain_tcp_connect():
    from mrag.db.qdrant import _accepts

    with patch("socket.create_connection") as connect:
        assert _accepts("127.0.0.1", 6333) is True
    assert connect.call_args.args == (("127.0.0.1", 6333),)
    with patch("socket.create_connection", side_effect=ConnectionRefusedError()):
        assert _accepts("127.0.0.1", 6333) is False
