import importlib.util
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "wait_for_version.py"
_spec = importlib.util.spec_from_file_location("wait_for_version", _PATH)
wfv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wfv)

TAG = "20261107-101500-abc1234"
FAST = ["--timeout", "1", "--interval", "0.05"]


@pytest.fixture
def server():
    """A local /health stub that answers from a list of (status, body) replies.

    The last reply repeats once the list is used up. Yields a function that
    takes the replies and returns the URL.
    """
    replies: list[tuple[int, bytes]] = []
    seen: list[int] = []

    class _Stub(BaseHTTPRequestHandler):
        def do_GET(self):
            status, body = replies[min(len(seen), len(replies) - 1)]
            seen.append(status)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()

    def start(*items):
        replies.extend(
            (s, b if isinstance(b, bytes) else json.dumps(b).encode()) for s, b in items
        )
        return f"http://127.0.0.1:{srv.server_port}/health"

    start.seen = seen
    yield start
    srv.shutdown()
    srv.server_close()


def run(url, expect=TAG, extra=FAST):
    return wfv.main(["--url", url, "--expect", expect, *extra])


def test_matching_version_exits_zero_with_one_line(server, capsys):
    url = server((200, {"status": "ok", "version": TAG}))

    assert run(url) == 0

    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert url in out and TAG in out


def test_version_that_never_matches_exits_nonzero_naming_both(server, capsys):
    url = server((200, {"status": "ok", "version": "dev"}))

    assert run(url) != 0

    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert err.startswith(f'expected {TAG}, got version "dev"')
    assert len(server.seen) > 1  # it retried


def test_missing_version_field_exits_nonzero(server, capsys):
    url = server((200, {"status": "ok"}))

    assert run(url) != 0

    assert capsys.readouterr().err.startswith(
        f"expected {TAG}, response has no version field"
    )


def test_503_then_match_exits_zero(server):
    url = server((503, {"detail": "starting"}), (200, {"status": "ok", "version": TAG}))

    assert run(url) == 0
    assert server.seen == [503, 200]


def test_not_json_and_old_version_are_retried(server):
    url = server(
        (200, b"<html>hi</html>"),
        (200, {"status": "ok", "version": "20261106-201341-2452847"}),
        (200, {"status": "ok", "version": TAG}),
    )

    assert run(url) == 0
    assert len(server.seen) == 3


def test_last_503_is_reported(server, capsys):
    url = server((503, {"detail": "down"}))

    assert run(url) != 0
    assert capsys.readouterr().err.startswith(f"expected {TAG}, HTTP 503")


def test_nothing_listening_exits_nonzero(capsys):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # The port is closed now, so connections are refused.

    start = time.monotonic()
    assert run(f"http://127.0.0.1:{port}/health") != 0
    assert time.monotonic() - start < 3

    assert capsys.readouterr().err.startswith(f"expected {TAG}, connection refused")


def test_defaults_are_180_seconds_and_5_seconds():
    assert wfv.DEFAULT_TIMEOUT == 180
    assert wfv.DEFAULT_INTERVAL == 5


def test_script_imports_only_the_standard_library():
    import ast
    import sys

    tree = ast.parse(_PATH.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module.split(".")[0])

    assert names and names <= set(sys.stdlib_module_names)
