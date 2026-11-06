"""Wait until a deployed app reports the expected version on /health.

    python3 scripts/wait_for_version.py --url https://rulesmith-dev.fly.dev/health \\
        --expect 20261107-101500-abc1234 [--timeout 180]

Requests --url every 5 s (--interval) until a response is HTTP 200 with JSON
`{"status": "ok", "version": <expected>}`, then prints one line and exits 0.
Connection errors, other status codes, bodies that are not JSON and a
different version (the old release still answering, or a cold start) are
retried. When --timeout seconds run out it prints one line describing the
last thing it saw and exits 1.

Both deploy workflows (`.github/workflows/deploy-dev.yml` and
`promote-prod.yml`) run it after `flyctl deploy`; see _docs/deployment.md.
Standard library only, so it runs on a bare runner without `uv sync`.
"""

import argparse
import http.client
import json
import socket
import sys
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 180.0
DEFAULT_INTERVAL = 5.0
REQUEST_TIMEOUT = 10.0  # per request, capped by the time left
MAX_BODY = 64 * 1024


def check(url: str, expect: str, request_timeout: float) -> tuple[bool, str]:
    """Make one request. Return (matched, what was seen)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=request_timeout) as resp:
            status = resp.status
            body = resp.read(MAX_BODY)
    except urllib.error.HTTPError as exc:
        exc.close()
        return False, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, _describe_error(exc.reason)
    except (OSError, http.client.HTTPException) as exc:
        return False, _describe_error(exc)

    if status != 200:
        return False, f"HTTP {status}"
    try:
        data = json.loads(body)
    except ValueError:
        return False, "response is not JSON"
    if not isinstance(data, dict):
        return False, "response is not a JSON object"
    if data.get("status") != "ok":
        return False, f"got status {json.dumps(data.get('status'))}"
    if "version" not in data:
        return False, "response has no version field"
    if data["version"] != expect:
        return False, f"got version {json.dumps(data['version'])}"
    return True, f"version {json.dumps(data['version'])}"


def _describe_error(err: object) -> str:
    if isinstance(err, ConnectionRefusedError):
        return "connection refused"
    if isinstance(err, (TimeoutError, socket.timeout)):
        return "request timed out"
    if isinstance(err, socket.gaierror):
        return "host not found"
    return f"connection error: {err}"


def wait(
    url: str,
    expect: str,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
) -> tuple[bool, str]:
    """Retry check() until it matches or timeout seconds have passed."""
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        matched, seen = check(url, expect, max(1.0, min(REQUEST_TIMEOUT, left)))
        if matched:
            return True, seen
        left = deadline - time.monotonic()
        if left <= 0:
            return False, seen
        time.sleep(min(interval, left))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True, help="the /health URL to poll")
    parser.add_argument("--expect", required=True, help="the version (tag) to wait for")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"seconds to keep trying (default {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"seconds between requests (default {DEFAULT_INTERVAL:g})",
    )
    args = parser.parse_args(argv)

    matched, seen = wait(args.url, args.expect, args.timeout, args.interval)
    if matched:
        print(f"ok: {args.url} reports {seen}")
        return 0
    print(
        f"expected {args.expect}, {seen} ({args.url}, gave up after {args.timeout:g}s)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
