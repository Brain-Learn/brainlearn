"""Redirect-policy adversarial coverage, fully offline on loopback.

A real local HTTP server emits redirect chains so the manual hop loop is
exercised end to end; no test here reaches past 127.0.0.1.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from brainlearn_core import (
    OpenNeuroDownloadSource,
    ProviderError,
    RedirectPolicy,
)
from brainlearn_core.openneuro import (
    _open_with_redirect_policy,
    _redirect_opener,
)

SECRET = "SUPERSECRET-token-value"


class _ChainHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, dict[str, str], bytes]] = {}
    hits: list[str] = []
    posts: list[tuple[str, bytes]] = []

    def log_message(self, *args: Any) -> None:
        pass

    def _record(self) -> bytes:
        type(self).hits.append(self.path)
        if self.command == "POST":
            length = int(self.headers.get("Content-Length", 0) or 0)
            seen = self.rfile.read(length)
            type(self).posts.append((self.path, seen))
            if self.path == "/post-echo":
                body = json.dumps({"data": {"method": "POST", "bytes": len(seen)}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return b""
        route = type(self).routes.get(self.path)
        if route is None:
            body = b"no such route"
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return b""
        status, headers, body = route
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value.format(port=type(self).server_port))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return body

    do_GET = _record
    do_POST = _record

    @classmethod
    def reset(cls, port: int) -> None:
        cls.hits = []
        cls.posts = []
        cls.server_port = port
        cls.routes = {
            "/final": (200, {}, b"final-bytes"),
            "/hop1": (302, {"Location": "/hop2"}, b""),
            "/hop2": (302, {"Location": "/final"}, b""),
            "/relative": (302, {"Location": "final"}, b""),
            "/garbage-body": (
                302,
                {"Location": "/final"},
                b"THIS BODY MUST NEVER BE PARSED",
            ),
            "/loop": (302, {"Location": "/loop"}, b""),
            "/creds": (
                302,
                {"Location": f"http://user:pass@127.0.0.1:{port}/final"},
                b"",
            ),
            "/signed": (302, {"Location": f"/final?token={SECRET}"}, b""),
            "/elsewhere": (302, {"Location": "https://example.invalid/final"}, b""),
            "/protocol-relative": (302, {"Location": "//example.invalid/final"}, b""),
            "/missing": (302, {}, b""),
            "/post-echo": (200, {"Content-Type": "application/json"}, b""),
            "/post-301": (301, {"Location": "/post-echo"}, b""),
            "/post-307": (307, {"Location": "/post-echo"}, b""),
            "/crn/datasets/ds000001/snapshots/1.0.0/files/plain.txt": (
                200,
                {},
                b"plain-file-bytes",
            ),
            "/crn/datasets/ds000001/snapshots/1.0.0/files/moved.txt": (
                302,
                {"Location": "/crn/datasets/ds000001/snapshots/1.0.0/files/plain.txt"},
                b"",
            ),
        }

    def _record_post_echo(self) -> None:
        """Documented no-op: POST echoes are answered inline in _record."""


@pytest.fixture()
def redirect_server() -> Any:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChainHandler)
    port = server.server_address[1]
    _ChainHandler.reset(port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _policy() -> RedirectPolicy:
    return RedirectPolicy(allowed_hosts=("127.0.0.1",), max_hops=5, allow_http=True)


def _get(server: Any, path: str, policy: RedirectPolicy | None = None) -> bytes:
    import urllib.request

    opener = _redirect_opener()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_address[1]}{path}",
        headers={"User-Agent": "test"},
        method="GET",
    )
    with _open_with_redirect_policy(
        opener, request, policy=policy or _policy(), timeout_s=5.0
    ) as response:
        return response.read()


def test_multi_hop_chain_resolves(redirect_server: Any) -> None:
    body = _get(redirect_server, "/hop1")
    assert body == b"final-bytes"
    assert _ChainHandler.hits == ["/hop1", "/hop2", "/final"]


def test_relative_location_resolves(redirect_server: Any) -> None:
    assert _get(redirect_server, "/relative") == b"final-bytes"


def test_redirect_body_is_never_consumed(redirect_server: Any) -> None:
    # The intermediate hop carries a non-JSON body; only the final JSON
    # would parse, proving bodies are validated hop-by-hop, not prefetched.
    assert _get(redirect_server, "/garbage-body") == b"final-bytes"


def test_loop_exceeds_bounded_hops(redirect_server: Any) -> None:
    import urllib.request

    opener = _redirect_opener()
    request = urllib.request.Request(
        f"http://127.0.0.1:{redirect_server.server_address[1]}/loop", method="GET"
    )
    with pytest.raises(ProviderError, match="too many times"):
        _open_with_redirect_policy(opener, request, policy=_policy(), timeout_s=5.0)
    assert len(_ChainHandler.hits) == 6  # initial + 5 bounded hops


def test_credential_and_signed_redirects_refused_without_leak(
    redirect_server: Any,
) -> None:
    with pytest.raises(ProviderError) as creds:
        _get(redirect_server, "/creds")
    assert "credentials" in str(creds.value)
    with pytest.raises(ProviderError) as signed:
        _get(redirect_server, "/signed")
    assert "signed address" in str(signed.value)
    with pytest.raises(ProviderError, match="approved hosts"):
        _get(redirect_server, "/elsewhere")
    # Protocol-relative URLs inherit the base scheme but must still match
    # the allowlist.
    with pytest.raises(ProviderError, match="approved hosts"):
        _get(redirect_server, "/protocol-relative")
    with pytest.raises(ProviderError, match="without a target"):
        _get(redirect_server, "/missing")
    # Neither refusal echoes the smuggled secret back to the caller.
    assert SECRET not in str(creds.value)
    assert SECRET not in str(signed.value)


def test_post_method_change_refused_but_307_preserved(redirect_server: Any) -> None:
    import urllib.request

    base = f"http://127.0.0.1:{redirect_server.server_address[1]}"
    opener = _redirect_opener()

    request = urllib.request.Request(
        f"{base}/post-301",
        data=b'{"q": 1}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(ProviderError, match="change the request method"):
        _open_with_redirect_policy(opener, request, policy=_policy(), timeout_s=5.0)

    preserved = urllib.request.Request(
        f"{base}/post-307",
        data=b'{"q": 1}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _open_with_redirect_policy(opener, preserved, policy=_policy(), timeout_s=5.0) as response:
        payload = json.loads(response.read().decode())
    assert payload == {"data": {"method": "POST", "bytes": 8}}
    assert _ChainHandler.posts[-1] == ("/post-echo", b'{"q": 1}')


def test_scheme_downgrade_refused_by_policy() -> None:
    from brainlearn_core.openneuro import _check_hop_url

    https_only = RedirectPolicy(allowed_hosts=("openneuro.org",))
    with pytest.raises(ProviderError, match="approved scheme"):
        _check_hop_url("http://openneuro.org/crn/graphql", https_only, "https")
    permissive = RedirectPolicy(allowed_hosts=("127.0.0.1",), allow_http=True)
    # Same-scheme plain HTTP hops are allowed under the loopback policy...
    _check_hop_url("http://127.0.0.1:9/x", permissive, "http")
    # ...but an https hop never downgrades, even then.
    with pytest.raises(ProviderError, match="approved scheme"):
        _check_hop_url("http://127.0.0.1:9/x", permissive, "https")
    with pytest.raises(ProviderError, match="approved scheme"):
        _check_hop_url("ftp://127.0.0.1:9/x", permissive, "http")


def test_file_source_follows_policy_redirects(redirect_server: Any) -> None:
    port = redirect_server.server_address[1]
    source = OpenNeuroDownloadSource(
        files_base_url=f"http://127.0.0.1:{port}/crn",
        redirect_policy=_policy(),
        timeout_s=5.0,
    )
    assert b"".join(source.stream_file("ds000001", "1.0.0", "plain.txt", 0)) == b"plain-file-bytes"
    assert b"".join(source.stream_file("ds000001", "1.0.0", "moved.txt", 0)) == b"plain-file-bytes"


def test_file_source_blocks_unlisted_hosts(redirect_server: Any) -> None:
    port = redirect_server.server_address[1]
    source = OpenNeuroDownloadSource(
        files_base_url=f"http://127.0.0.1:{port}/crn",
        redirect_policy=RedirectPolicy(allowed_hosts=("openneuro.org",), allow_http=True),
        timeout_s=5.0,
    )
    with pytest.raises(ProviderError, match="approved hosts"):
        b"".join(source.stream_file("ds000001", "1.0.0", "plain.txt", 0))
