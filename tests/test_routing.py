import asyncio
import json
import math
import stat
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import pytest

from routing import backends, policy, settings
from routing.judge import Judge, JudgeError, Verdict, distribution
from routing.ledger import Ledger
from routing.tools import Router, register

TZ = "Europe/Warsaw"


def lp(p):
    return math.log(p)


def cfg(**over):
    c = settings._merge(settings.DEFAULTS, over)
    c["claude"]["week"] = {"reset_weekday": 0, "reset_hour": 9, "timezone": TZ}
    return c


def at(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=ZoneInfo(TZ)).timestamp()


# -- judge -------------------------------------------------------------------

def test_distribution_folds_token_variants_and_reports_coverage():
    top = [{"token": " Y", "logprob": lp(0.5)}, {"token": "y", "logprob": lp(0.2)},
           {"token": "N", "logprob": lp(0.1)}, {"token": "Maybe", "logprob": lp(0.2)}]
    a = distribution(top, ("Y", "N"))
    assert a.probs["Y"] == pytest.approx(0.7 / 0.8)
    assert a.coverage == pytest.approx(0.8)


def test_distribution_without_any_option_is_an_error():
    with pytest.raises(JudgeError):
        distribution([{"token": "hello", "logprob": 0.0}], ("Y", "N"))


class FakeLlama(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLlama.requests.append(body)
        question = body["messages"][1]["content"]
        if "one digit" in question:
            top = [{"token": "1", "logprob": lp(0.6)}, {"token": "2", "logprob": lp(0.3)},
                   {"token": "0", "logprob": lp(0.1)}]
        else:
            top = [{"token": "Y", "logprob": lp(0.8)}, {"token": "N", "logprob": lp(0.2)}]
        payload = {"choices": [{"logprobs": {"content": [{"token": top[0]["token"], "top_logprobs": top}]}}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def llama():
    server = HTTPServer(("127.0.0.1", 0), FakeLlama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeLlama.requests.clear()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def test_judge_asks_two_one_token_questions(llama):
    v = Judge(llama, "judge").judge("Fix the typo in README.md; check: grep -q 'receive' README.md")
    assert v.p_local == pytest.approx(0.8)
    assert v.expected_difficulty == pytest.approx(0.6 * 1 + 0.3 * 2)
    assert len(FakeLlama.requests) == 2
    req = FakeLlama.requests[0]
    assert req["model"] == "judge" and req["max_tokens"] == 1 and req["logprobs"] is True
    assert req["chat_template_kwargs"] == {"enable_thinking": False}


def test_judge_unreachable_raises_judge_error():
    with pytest.raises(JudgeError):
        Judge("http://127.0.0.1:9/v1", "judge", timeout_s=1).judge("x")


# -- policy ------------------------------------------------------------------

def test_window_starts_at_last_reset():
    start, end = policy.window_bounds(at(2026, 9, 26), 0, 9, TZ)  # Saturday
    assert datetime.fromtimestamp(start, ZoneInfo(TZ)) == datetime(2026, 9, 21, 9, tzinfo=ZoneInfo(TZ))
    assert end - start == pytest.approx(7 * 86400, abs=3600)  # DST can shift an hour


def test_window_before_reset_hour_belongs_to_previous_week():
    start, _ = policy.window_bounds(at(2026, 9, 21, 8), 0, 9, TZ)  # Monday 08:00
    assert datetime.fromtimestamp(start, ZoneInfo(TZ)).day == 14


def verdict(p_local, expected):
    return Verdict(p_local=p_local, difficulty={}, expected_difficulty=expected, coverage=1.0)


AVAILABLE = policy.ClaudeState(unavailable_until=None)
LOCKED_OUT = policy.ClaudeState(unavailable_until=at(2026, 9, 28, 9))


def test_near_certain_task_starts_on_coder_then_claude_with_one_retry():
    d = policy.decide(verdict(0.9, 0.3), AVAILABLE, cfg())
    assert d.rung == "coder" and d.chain == ["coder", "claude", "claude"]


def test_merely_likely_task_starts_on_claude():
    d = policy.decide(verdict(0.7, 0.3), AVAILABLE, cfg())
    assert d.rung == "claude" and d.chain == ["claude", "claude"]


def test_confident_judge_but_not_trivial_task_starts_on_claude():
    d = policy.decide(verdict(0.9, 0.8), AVAILABLE, cfg())
    assert d.rung == "claude"


def test_locked_out_with_fair_chance_tries_coder_then_openrouter():
    d = policy.decide(verdict(0.4, 2.0), LOCKED_OUT, cfg())
    assert d.rung == "coder" and d.chain == ["coder", "openrouter"]
    assert "unavailable until Mon 28 Sep 09:00" in d.reason


def test_locked_out_and_unlikely_goes_straight_to_openrouter():
    d = policy.decide(verdict(0.2, 2.8), LOCKED_OUT, cfg())
    assert d.rung == "openrouter" and d.chain == ["openrouter"]


def test_locked_out_without_openrouter_leaves_only_the_coder():
    d = policy.decide(verdict(0.1, 2.8), LOCKED_OUT, cfg(openrouter={"enabled": False}))
    assert d.rung == "coder" and d.chain == ["coder"]


def test_judge_down_starts_on_claude():
    d = policy.decide(None, AVAILABLE, cfg(), judge_error="connection refused")
    assert d.rung == "claude" and "connection refused" in d.reason


def test_judge_down_while_locked_out_is_treated_as_pessimistic():
    d = policy.decide(None, LOCKED_OUT, cfg(), judge_error="connection refused")
    assert d.rung == "openrouter"


def test_thresholds_come_from_config():
    c = cfg(policy={"local_threshold": 0.6, "local_max_difficulty": 1.5, "fallback_threshold": 0.5})
    assert policy.decide(verdict(0.7, 1.2), AVAILABLE, c).rung == "coder"
    assert policy.decide(verdict(0.4, 2.0), LOCKED_OUT, c).rung == "openrouter"


def test_paid_rung_is_claude_until_locked_out():
    assert policy.paid_rung(AVAILABLE, cfg())[0] == "claude"
    assert policy.paid_rung(LOCKED_OUT, cfg())[0] == "openrouter"


def test_limit_hit_makes_claude_unavailable_until_it_lapses(tmp_path):
    led = Ledger(tmp_path / "l.db")
    led.add_limit_hit(0, 0, at(2026, 9, 28, 9))
    assert not policy.claude_state(led, at(2026, 9, 26)).available
    assert policy.claude_state(led, at(2026, 9, 28, 10)).available


def test_old_ledger_gains_claude_column_and_keeps_rows(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, ts REAL NOT NULL, brief TEXT NOT NULL, workdir TEXT,"
                " rung TEXT NOT NULL, reason TEXT NOT NULL, verdict TEXT, pace TEXT)")
    old.execute("INSERT INTO decisions VALUES ('a', 1, 'b', NULL, 'coder', 'r', NULL, '{}')")
    old.commit()
    old.close()
    led = Ledger(path)
    new_id = led.add_decision("brief", None, "claude", "why", None, {"available": True})
    assert led.decision("a")["rung"] == "coder"
    assert json.loads(led.decision(new_id)["claude"]) == {"available": True}


# -- backends ----------------------------------------------------------------

def test_parse_detects_limit_hit():
    out = json.dumps({"type": "result", "is_error": True, "result": "Claude usage limit reached. Your limit resets at 9am"})
    r = backends.parse("claude", out, "", 1, 3.0)
    assert r.limit_hit and not r.ok


def test_parse_success_reads_cost_and_turns():
    out = json.dumps({"is_error": False, "result": "done", "total_cost_usd": 1.25, "num_turns": 7, "session_id": "s"})
    r = backends.parse("claude", out, "", 0, 3.0)
    assert r.ok and r.cost == 1.25 and r.turns == 7 and not r.limit_hit


def test_subscription_env_strips_api_billing_vars():
    env = backends.environment(cfg(), "claude", {"ANTHROPIC_API_KEY": "sk", "PATH": "/bin"})
    assert "ANTHROPIC_API_KEY" not in env and env["PATH"] == "/bin"


def test_openrouter_env_points_claude_code_at_gateway(tmp_path):
    c = cfg(openrouter={"config_dir": str(tmp_path / "cc")})
    env = backends.environment(c, "openrouter", {"OPENROUTER_API_KEY": "or-key"})
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "or-key" and env["ANTHROPIC_API_KEY"] == ""
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cc")


def test_openrouter_without_key_is_refused():
    with pytest.raises(backends.BackendError):
        backends.environment(cfg(), "openrouter", {})


def test_workdir_outside_roots_is_refused(tmp_path):
    (tmp_path / "ok").mkdir()
    assert backends.check_workdir(str(tmp_path / "ok"), [str(tmp_path)])
    with pytest.raises(backends.BackendError):
        backends.check_workdir("/", [str(tmp_path)])


# -- tools end to end --------------------------------------------------------

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
if os.environ.get("ANTHROPIC_BASE_URL"):
    print(json.dumps({"is_error": False, "result": "done via openrouter", "total_cost_usd": 0.4, "num_turns": 3}))
else:
    mode = open(os.environ["FAKE_CLAUDE_MODE"]).read().strip()
    if mode == "limit":
        print(json.dumps({"is_error": True, "result": "You've hit your weekly usage limit. Resets Monday 9am"}))
        sys.exit(1)
    print(json.dumps({"is_error": False, "result": "done via max", "total_cost_usd": 2.5, "num_turns": 9}))
"""


class FakeJudge:
    def __init__(self, v):
        self.v = v

    def judge(self, brief):
        if self.v is None:
            raise JudgeError("down")
        return self.v


@pytest.fixture
def router(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    mode = tmp_path / "mode"
    mode.write_text("ok")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", str(mode))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    (tmp_path / "proj").mkdir()
    c = cfg(claude={"bin": str(claude), "workdir_roots": [str(tmp_path)]},
            openrouter={"config_dir": str(tmp_path / "cc")})
    state = {"verdict": verdict(0.9, 0.5)}
    r = Router(clock=lambda: at(2026, 9, 24), judge_factory=lambda _c: FakeJudge(state["verdict"]),
               ledger=Ledger(tmp_path / "ledger.db"), config_loader=lambda: c)
    r.test = {"mode": mode, "proj": str(tmp_path / "proj"), "state": state}
    return r


def call(fn, **args):
    out = fn(args)
    if asyncio.iscoroutine(out):
        out = asyncio.run(out)
    return json.loads(out)


def test_route_then_outcome_labels_the_coder_attempt(router):
    d = call(router.route, brief="small fix", workdir=router.test["proj"])
    assert d["rung"] == "coder" and d["decision_id"]
    assert call(router.outcome, decision_id=d["decision_id"], rung="coder", verified=True)["recorded"]
    assert router.ledger.stats(0)["coder"]["verified"] == 1


def test_escalate_runs_claude_and_records_cost(router):
    router.test["state"]["verdict"] = verdict(0.2, 2.2)
    d = call(router.route, brief="big feature", workdir=router.test["proj"])
    assert d["rung"] == "claude"
    r = call(router.escalate, brief="big feature", workdir=router.test["proj"], decision_id=d["decision_id"])
    assert r["ok"] and r["backend"] == "claude" and r["runs"][0]["cost"] == 2.5
    assert router.ledger.claude_spent(0) == 2.5


def test_limit_hit_mid_run_falls_back_to_openrouter_and_blocks_claude(router):
    router.test["mode"].write_text("limit")
    r = call(router.escalate, brief="task", workdir=router.test["proj"])
    assert [run["rung"] for run in r["runs"]] == ["claude", "openrouter"]
    assert r["backend"] == "openrouter" and r["ok"]
    router.test["state"]["verdict"] = verdict(0.1, 2.9)
    assert call(router.route, brief="another hard task")["rung"] == "openrouter"
    assert call(router.status)["claude"]["available"] is False


def test_route_with_judge_down_still_decides(router):
    router.test["state"]["verdict"] = None
    d = call(router.route, brief="anything")
    assert d["rung"] == "claude" and d["judge"] is None


def test_route_records_claude_availability_on_the_decision(router):
    d = call(router.route, brief="small fix")
    assert d["claude"]["available"] is True
    assert json.loads(router.ledger.decision(d["decision_id"])["claude"])["available"] is True


def test_escalate_rejects_workdir_outside_roots(router):
    assert "outside the allowed roots" in call(router.escalate, brief="x", workdir="/")["error"]


def test_register_puts_all_tools_in_coding_toolset():
    seen = []

    class Ctx:
        def register_tool(self, **kw):
            seen.append((kw["name"], kw["toolset"], kw.get("is_async", False)))

    register(Ctx(), router=object.__new__(Router))
    assert seen == [("route", "coding", False), ("escalate", "coding", True),
                    ("route_outcome", "coding", False), ("routing_status", "coding", False)]


def test_settings_merge_routing_yaml(tmp_path):
    f = tmp_path / "routing.yaml"
    f.write_text("policy:\n  local_threshold: 0.8\nopenrouter:\n  enabled: false\n")
    c = settings.load(f)
    assert c["policy"]["local_threshold"] == 0.8 and c["policy"]["fallback_threshold"] == 0.3
    assert c["openrouter"]["enabled"] is False
