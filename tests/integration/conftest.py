"""Fixtures for integration tests that drive the real app over HTTP.

These tests talk to a running Docker Compose stack at RULESMITH_BASE_URL.
They never import the app or the database layer. Run them with
`make test-integration`.
"""

import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("RULESMITH_BASE_URL", "http://localhost:8000").rstrip("/")
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
ORDERS_CSV = FIXTURES / "integration" / "orders.csv"
MALFORMED_CSV = FIXTURES / "loader" / "malformed.csv"

HEALTH_TIMEOUT_SECONDS = 60


@pytest.fixture(scope="session", autouse=True)
def _stack_reachable():
    try:
        httpx.get(f"{BASE_URL}/health", timeout=5).raise_for_status()
    except httpx.HTTPError:
        pytest.exit(
            f"Integration stack is not reachable at {BASE_URL}/health. "
            "Start it (make test-integration does this) or set RULESMITH_BASE_URL.",
            returncode=1,
        )


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, follow_redirects=True, timeout=30) as c:
        yield c


def unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def create_dataset(client: httpx.Client) -> tuple[int, str]:
    name = unique_name("it")
    resp = client.post("/datasets", data={"name": name})
    assert resp.status_code == 200, resp.status_code
    match = re.search(r"/datasets/(\d+)$", str(resp.url))
    assert match, "create dataset did not redirect to a dataset page"
    return int(match.group(1)), name


def add_rule(client: httpx.Client, dataset_id: int, name: str, expression: str) -> None:
    resp = client.post(
        f"/datasets/{dataset_id}/rules", data={"name": name, "expression": expression}
    )
    assert resp.status_code == 200, resp.status_code


def upload(client: httpx.Client, dataset_id: int, path: Path, filename: str | None = None):
    return client.post(
        f"/datasets/{dataset_id}/uploads",
        files={"file": (filename or path.name, path.read_bytes())},
    )


def count_runs(client: httpx.Client, dataset_id: int) -> int:
    page = client.get(f"/datasets/{dataset_id}").text
    return len(set(re.findall(rf"/datasets/{dataset_id}/runs/(\d+)\"", page)))


@dataclass
class Verdict:
    name: str
    verdict: str
    counts: str
    failing_rows_url: str | None


def parse_run(html: str) -> list[Verdict]:
    """Extract (rule name, verdict, counts, failing-rows url) from a run page."""
    out = []
    for block in html.split('<li class="rule-result rule-result--')[1:]:
        verdict = block.split('"', 1)[0]
        name = re.search(r"<strong>(.*?)</strong>", block, re.S).group(1).strip()
        counts = re.search(r'<span class="counts">(.*?)</span>', block, re.S)
        url = re.search(r'hx-get="([^"]+)"', block)
        out.append(
            Verdict(
                name,
                verdict,
                counts.group(1).strip() if counts else "",
                url.group(1) if url else None,
            )
        )
    return out


@dataclass
class HappyPath:
    dataset_id: int
    dataset_name: str
    run_url: str
    pass_rule: str
    fail_rule: str


@pytest.fixture(scope="session")
def happy(client) -> HappyPath:
    """Scenario 1 data, shared so scenario 3 can reuse it (or run alone)."""
    dataset_id, name = create_dataset(client)
    pass_rule = unique_name("all-nonzero")
    fail_rule = unique_name("positive-amount")
    add_rule(client, dataset_id, pass_rule, "amount != 0")
    add_rule(client, dataset_id, fail_rule, "amount > 0")
    resp = upload(client, dataset_id, ORDERS_CSV)
    assert resp.status_code == 200, resp.status_code
    return HappyPath(dataset_id, name, str(resp.url), pass_rule, fail_rule)


def wait_for_health(timeout: float = HEALTH_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{BASE_URL}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    pytest.fail(f"app did not return 200 from /health within {timeout}s")


def restart_app() -> None:
    subprocess.run(
        ["docker", "compose", "restart", "app"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
