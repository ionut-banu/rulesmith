"""Trip the RulesmithChecksFailing alert against a local app.

Uploads a tiny fixed valid CSV to a dataset about once a second for about
three minutes and reports how many requests returned 500. Exits non-zero if
the app is unreachable or no 500 was seen (the failure switch is not on).

Start the app with the switch on and telemetry exporting (local stack from
`make obs-up`), for example:

    RULESMITH_FAIL_UPLOADS=1 OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \\
        uv run uvicorn rulesmith.app:app

or, for the Docker stack:

    RULESMITH_FAIL_UPLOADS=1 OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318 make up

Then run `make simulate-failures` (or this script). Turn the switch off again
by restarting the app without RULESMITH_FAIL_UPLOADS. Only --base-url is
contacted. Test-only: the switch is refused when RULESMITH_ENV is prod.
"""

import argparse
import sys
import time
import urllib.error
import urllib.request
import uuid

CSV = b"id,amount\n1,10\n2,20\n"  # fixed, tiny, no real data


def _request(url: str, data: bytes | None = None, headers: dict | None = None):
    """Return (status, headers); HTTP errors are returned, not raised."""
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _multipart(filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{filename}\"\r\nContent-Type: text/csv\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--duration", type=float, default=180, help="seconds (default 180)")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between uploads")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")

    # Do not follow redirects: a 303 is a successful upload.
    urllib.request.install_opener(urllib.request.build_opener(_NoRedirect))

    try:
        status, _ = _request(f"{base}/health")
        if status != 200:
            raise urllib.error.URLError(f"/health returned {status}")
        # Create a dataset with a form POST (303 -> its page).
        form = b"name=" + f"simulation-{uuid.uuid4().hex[:8]}".encode()
        status, headers = _request(
            f"{base}/datasets",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        location = headers.get("Location", "")
        if status != 303 or not location.startswith("/datasets/"):
            print(f"Could not create a dataset (HTTP {status}).", file=sys.stderr)
            return 1
        dataset_id = int(location.rsplit("/", 1)[1])
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"Cannot reach the app at {base}: {exc}. Is it running?", file=sys.stderr)
        return 1

    sent = errors = 0
    deadline = time.monotonic() + args.duration
    print(f"Uploading to dataset {dataset_id} for {args.duration:.0f}s ...")
    while time.monotonic() < deadline:
        payload, ctype = _multipart("simulation.csv", CSV)
        try:
            status, _ = _request(
                f"{base}/datasets/{dataset_id}/uploads",
                data=payload,
                headers={"Content-Type": ctype},
            )
        except (urllib.error.URLError, OSError) as exc:
            print(f"Lost the app at {base}: {exc}", file=sys.stderr)
            return 1
        sent += 1
        errors += status == 500
        time.sleep(args.interval)

    print(f"Sent {sent} uploads, {errors} returned 500.")
    if errors == 0:
        print(
            "No 500 responses: start the app with RULESMITH_FAIL_UPLOADS=1 "
            "(see this script's header).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
