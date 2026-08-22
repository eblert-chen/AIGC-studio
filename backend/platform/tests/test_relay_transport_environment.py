from __future__ import annotations

import http.server
import threading

import pytest

from platform_api.relay_client import (
    HttpxRelayClient,
    HttpxRelayOperationsClient,
    RelayTemporaryError,
)


class _RecordingProxy(http.server.BaseHTTPRequestHandler):
    requests: list[set[str]] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        type(self).requests.append({name.casefold() for name in self.headers})
        self.send_response(502)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        return


def test_relay_clients_never_send_credentials_to_an_ambient_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingProxy.requests = []
    proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingProxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    proxy_url = f"http://127.0.0.1:{proxy.server_port}"
    monkeypatch.setenv("HTTP_PROXY", proxy_url)
    monkeypatch.setenv("HTTPS_PROXY", proxy_url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    relay = HttpxRelayClient(
        base_url="http://127.0.0.1:1",
        client_id="synthetic-platform-client",
        api_key="synthetic-relay-key-material",
        timeout_seconds=0.2,
        allow_local_http=True,
    )
    operations = HttpxRelayOperationsClient(
        base_url="http://127.0.0.1:1",
        tenant_id="51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
        operations_token="synthetic-operations-token-material-1234",
        approval_key_id="synthetic-approval-v1",
        approval_secret="synthetic-independent-approval-secret-1234",
        timeout_seconds=0.2,
    )
    try:
        with pytest.raises(RelayTemporaryError):
            relay.get_model_catalog()
        with pytest.raises(RelayTemporaryError):
            operations.list_channels()
    finally:
        relay.close()
        operations.close()
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=2)

    assert _RecordingProxy.requests == []

