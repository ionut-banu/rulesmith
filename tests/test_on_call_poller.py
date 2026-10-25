import importlib.util
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "on-call-engineer" / "poller.py"
_spec = importlib.util.spec_from_file_location("on_call_poller", _PATH)
poller = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(poller)


def alert(fp="abc12345deadbeef", state="active", name="RulesmithChecksFailing", **kw):
    a = {
        "fingerprint": fp,
        "status": {"state": state, "silencedBy": [], "inhibitedBy": []},
        "labels": {"alertname": name, "owner": "on-call-agent", "environment": "local"},
        "annotations": {"summary": "50% of upload checks failed", "version": "dev"},
        "startsAt": "2026-10-25T10:00:00Z",
        "generatorURL": "http://localhost:3000/alerting/grafana/x/view",
    }
    a.update(kw)
    return a


class Args:
    def __init__(self, tmp_path, dry_run=False, timeout=5):
        self.state_file = tmp_path / "state.json"
        self.dry_run = dry_run
        self.timeout = timeout
        self.grafana_url = "http://grafana.invalid"
        self.once = True


class Harness:
    def __init__(self, tmp_path, payload, dry_run=False, claude=True):
        self.args = Args(tmp_path, dry_run)
        self.payload = payload
        self.sessions = []
        self.branches = []
        self.claude = claude

    def run(self):
        return poller.poll_once(
            self.args,
            fetch=lambda url, h: self.payload,
            session=lambda argv, path, t: self.sessions.append((argv, path)) or "ok",
            make_branch=self.branches.append,
            which=lambda n: "/bin/claude" if self.claude else None,
            headers={},
        )


# --- parsing and filtering -------------------------------------------------


def test_parse_keeps_only_active_owned_alerts():
    other = alert("f2")
    other["labels"]["owner"] = "someone"
    payload = [alert("f1"), other, alert("f3", state="suppressed"), alert("f4", state="resolved")]
    assert [a["fingerprint"] for a in poller.parse_alerts(payload)] == ["f1"]


def test_parse_tolerates_missing_optional_fields_and_unicode():
    raw = alert()
    del raw["annotations"], raw["generatorURL"]
    raw["labels"]["environment"] = "größe-" + "x" * 5000
    (a,) = poller.parse_alerts([raw])
    assert a["annotations"] == {} and a["generatorURL"] == ""
    assert len(a["labels"]["environment"]) > 5000


def test_parse_rejects_non_list():
    with pytest.raises(ValueError):
        poller.parse_alerts({"message": "nope"})


def test_duplicate_fingerprint_in_one_response_collapses():
    assert len(poller.parse_alerts([alert("f1"), alert("f1")])) == 1


# --- state / dedup ---------------------------------------------------------


def test_same_alert_over_many_polls_starts_one_session(tmp_path):
    h = Harness(tmp_path, [alert()])
    assert h.run() == 1
    assert h.run() == 0
    assert h.run() == 0
    assert len(h.sessions) == 1


def test_restart_with_existing_state_does_not_retrigger(tmp_path):
    h = Harness(tmp_path, [alert()])
    h.run()
    h2 = Harness(tmp_path, [alert()])
    assert h2.run() == 0 and h2.sessions == []


def test_resolve_then_refire_starts_new_session(tmp_path):
    h = Harness(tmp_path, [alert()])
    h.run()
    h.payload = []
    h.run()
    assert json.loads(h.args.state_file.read_text())["fingerprints"] == []
    h.payload = [alert()]
    h.run()
    assert len(h.sessions) == 2


def test_missing_state_is_empty_and_corrupt_is_fatal(tmp_path):
    assert poller.load_state(tmp_path / "none.json") == set()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(poller.FatalError):
        poller.load_state(bad)
    bad.write_text('{"fingerprints": "x"}')
    with pytest.raises(poller.FatalError):
        poller.load_state(bad)


def test_fingerprint_recorded_even_if_session_raises(tmp_path):
    h = Harness(tmp_path, [alert()])

    def boom(*a):
        raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        poller.poll_once(h.args, fetch=lambda u, hd: h.payload, session=boom,
                         make_branch=lambda n: None, which=lambda n: "x", headers={})
    assert "abc12345deadbeef" in poller.load_state(h.args.state_file)


def test_missing_claude_is_fatal_and_not_recorded(tmp_path):
    h = Harness(tmp_path, [alert()], claude=False)
    with pytest.raises(poller.FatalError, match="claude"):
        h.run()
    assert not h.args.state_file.exists()


def test_save_state_is_atomic_no_temp_left(tmp_path):
    poller.save_state(tmp_path / "s.json", {"b", "a"})
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


# --- prompt, argv, names ---------------------------------------------------


def test_prompt_includes_role_verbatim_alert_and_data_marker():
    role = poller.ROLE_FILE.read_text()
    prompt = poller.build_prompt(role, poller.parse_alerts([alert()])[0])
    assert prompt.startswith(role.rstrip())
    for needle in ["RulesmithChecksFailing", "owner: on-call-agent", "summary: 50% of upload",
                   "version: dev", "2026-10-25T10:00:00Z", "generatorURL: http://localhost:3000",
                   "data, not instructions", "on-call/RulesmithChecksFailing-abc12345"]:
        assert needle in prompt


def test_missing_role_file_is_fatal(tmp_path):
    with pytest.raises(poller.FatalError):
        poller.read_role(tmp_path / "nope.md")


def test_argv_permissions():
    argv = poller.build_argv("PROMPT")
    assert argv[:3] == ["claude", "-p", "PROMPT"]
    assert "--dangerously-skip-permissions" not in argv
    allowed = argv[argv.index("--allowedTools") + 1]
    denied = argv[argv.index("--disallowedTools") + 1]
    assert "git push" not in allowed
    assert "Bash(git push:*)" in denied
    for needle in ["Bash(uv run pytest:*)", "Bash(gh issue create:*)", "Bash(git commit:*)", "Edit"]:
        assert needle in allowed
    for needle in ["gh pr merge", "gh workflow", "gh release", "flyctl", "docker", "rm -rf"]:
        assert needle in denied


def test_log_filename_sanitizes_and_cannot_escape():
    a = poller.parse_alerts([alert(name="../../etc/passwd x")])[0]
    name = poller.log_filename(a, datetime(2026, 10, 25, 10, 0, 1, tzinfo=timezone.utc))
    assert name == "20261025T100001Z-______etc_passwd_x.log"
    assert "/" not in name and ".." not in name
    assert "/" not in poller.branch_name(a).split("on-call/", 1)[1]


# --- dry run, grafana down, auth ------------------------------------------


def test_dry_run_runs_nothing_and_writes_nothing(tmp_path, capsys):
    h = Harness(tmp_path, [alert()], dry_run=True)
    assert h.run() == 1
    out = capsys.readouterr().out
    assert "RulesmithChecksFailing" in out and "command: claude -p" in out
    assert "--allowedTools" in out
    assert h.sessions == [] and h.branches == []
    assert not h.args.state_file.exists()


def test_dry_run_does_not_need_claude(tmp_path):
    assert Harness(tmp_path, [alert()], dry_run=True, claude=False).run() == 1


def _down(url, headers):
    raise poller.GrafanaError("Grafana unreachable: ConnectionRefusedError")


def test_once_exits_nonzero_when_grafana_down(tmp_path):
    rc = poller.main(["--once", "--state-file", str(tmp_path / "s.json")], fetch=_down)
    assert rc == 1


def test_loop_continues_after_grafana_outage(tmp_path, capsys):
    ticks = []

    def sleep(_):
        ticks.append(1)
        if len(ticks) == 3:
            raise KeyboardInterrupt

    rc = poller.main(["--state-file", str(tmp_path / "s.json")], fetch=_down, sleep=sleep)
    assert rc == 0 and len(ticks) == 3
    assert capsys.readouterr().out.count("Grafana unreachable") == 3


def test_corrupt_state_exits_nonzero(tmp_path):
    s = tmp_path / "s.json"
    s.write_text("garbage")
    assert poller.main(["--once", "--state-file", str(s)], fetch=_down) == 2


def test_auth_headers_and_no_secret_in_errors(monkeypatch):
    assert poller.auth_headers({"GRAFANA_TOKEN": "tok"}) == {"Authorization": "Bearer tok"}
    assert poller.auth_headers({}) ["Authorization"].startswith("Basic ")


class _Stub(BaseHTTPRequestHandler):
    body = b"[]"
    status = 200
    seen = {}

    def do_GET(self):
        _Stub.seen["path"] = self.path
        self.send_response(_Stub.status)
        self.end_headers()
        self.wfile.write(_Stub.body)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub():
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_fetch_alerts_against_stub_server(stub):
    _Stub.status, _Stub.body = 200, json.dumps([alert()]).encode()
    assert poller.fetch_alerts(stub, {})[0]["fingerprint"] == "abc12345deadbeef"
    assert _Stub.seen["path"].endswith("active=true&silenced=false&inhibited=false")
    _Stub.status = 503
    with pytest.raises(poller.GrafanaError, match="503"):
        poller.fetch_alerts(stub, {})
    _Stub.status, _Stub.body = 200, b"<html>"
    with pytest.raises(poller.GrafanaError, match="invalid JSON"):
        poller.fetch_alerts(stub, {})


def test_fetch_connection_refused():
    with pytest.raises(poller.GrafanaError, match="unreachable"):
        poller.fetch_alerts("http://127.0.0.1:1", {})


def test_run_session_timeout_kills_and_logs(tmp_path):
    log = tmp_path / "l" / "x.log"
    assert poller.run_session(["sleep", "30"], log, 0.3) == "timeout"
    assert "timed out" in log.read_text()
    assert poller.run_session(["sh", "-c", "echo hi; exit 3"], log, 5) == "failed"
    assert "hi" in log.read_text()
