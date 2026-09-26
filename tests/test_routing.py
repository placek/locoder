import asyncio
import json
import math
import stat
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import pytest

from routing import backends, policy, settings
from routing.judge import (Answer, Choice, Judge, JudgeError, Noul, Score, SemIfBackend, Verdict,
                           distribution, render)
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
        elif "one letter" in question:
            top = [{"token": " B", "logprob": lp(0.5)}, {"token": "A", "logprob": lp(0.3)},
                   {"token": "Sure", "logprob": lp(0.2)}]
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
    v = Judge(SemIfBackend(llama, "judge")).judge("Fix the typo in README.md; check: grep -q 'receive' README.md")
    assert v.p_local == pytest.approx(0.8)
    assert v.expected_difficulty == pytest.approx(0.6 * 1 + 0.3 * 2)
    assert len(FakeLlama.requests) == 2
    req = FakeLlama.requests[0]
    assert req["model"] == "judge" and req["max_tokens"] == 1 and req["logprobs"] is True
    assert req["chat_template_kwargs"] == {"enable_thinking": False}


def test_judge_unreachable_raises_judge_error():
    with pytest.raises(JudgeError):
        Judge(SemIfBackend("http://127.0.0.1:9/v1", "judge", timeout_s=1)).judge("x")


def test_render_gives_each_question_type_single_token_answers():
    prompt, tokens = render(Noul("Is it urgent?"))
    assert prompt.endswith("Answer Y or N.") and tokens == {"Y": "yes", "N": "no"}
    prompt, tokens = render(Score("How hard?", {0: "trivial", 3: "heavy"}))
    assert "0 = trivial\n3 = heavy" in prompt and prompt.endswith("one digit.") and tokens == {"0": "0", "3": "3"}
    prompt, tokens = render(Choice("Which team?", {"billing": "charges", "support": "the rest"}))
    assert "A = billing: charges\nB = support: the rest" in prompt and tokens == {"A": "billing", "B": "support"}
    with pytest.raises(ValueError):
        render(Score("x", {10: "too many digits"}))


def test_semif_answers_a_choice_by_option_name(llama):
    a = SemIfBackend(llama, "judge").system_one("my invoice was charged twice",
                                                {"team": Choice("Which team?", {"billing": "charges", "support": "rest"})})
    assert a["team"].probs == pytest.approx({"billing": 0.3 / 0.8, "support": 0.5 / 0.8})   # A, B
    assert a["team"].coverage == pytest.approx(0.8)


def test_any_backend_with_system_one_can_judge():
    class Canned:
        def system_one(self, state, questions):
            assert set(questions) == {"difficulty", "local"} and "typo" in state
            return {"difficulty": Answer({"0": 0.5, "1": 0.5}, coverage=0.9),
                    "local": Answer({"yes": 0.8, "no": 0.2}, coverage=0.7)}

    v = Judge(Canned()).judge("fix a typo")
    assert v.p_local == 0.8 and v.expected_difficulty == 0.5 and v.coverage == 0.7


# -- policy ------------------------------------------------------------------

def test_window_starts_at_last_reset():
    start, end = policy.window_bounds(at(2026, 9, 26), 0, 9, TZ)  # Saturday
    assert datetime.fromtimestamp(start, ZoneInfo(TZ)) == datetime(2026, 9, 21, 9, tzinfo=ZoneInfo(TZ))
    assert end - start == pytest.approx(7 * 86400, abs=3600)  # DST can shift an hour


def test_window_before_reset_hour_belongs_to_previous_week():
    start, _ = policy.window_bounds(at(2026, 9, 21, 8), 0, 9, TZ)  # Monday 08:00
    assert datetime.fromtimestamp(start, ZoneInfo(TZ)).day == 14


def verdict(p_local, expected, coverage=1.0):
    return Verdict(p_local=p_local, difficulty={}, expected_difficulty=expected, coverage=coverage)


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


def test_low_coverage_verdict_is_treated_as_no_verdict():
    d = policy.decide(verdict(0.95, 0.1, coverage=0.3), AVAILABLE, cfg())
    assert d.rung == "claude" and "coverage 0.30 is below policy.min_coverage" in d.reason
    d = policy.decide(verdict(0.95, 0.1, coverage=0.3), LOCKED_OUT, cfg())
    assert d.rung == "openrouter"
    assert policy.decide(verdict(0.95, 0.1, coverage=0.5), AVAILABLE, cfg()).rung == "coder"
    assert policy.decide(verdict(0.95, 0.1, coverage=0.3), AVAILABLE, cfg(policy={"min_coverage": 0.2})).rung == "coder"


def test_thresholds_come_from_config():
    c = cfg(policy={"local_threshold": 0.6, "local_max_difficulty": 1.5, "fallback_threshold": 0.5})
    assert policy.decide(verdict(0.7, 1.2), AVAILABLE, c).rung == "coder"
    assert policy.decide(verdict(0.4, 2.0), LOCKED_OUT, c).rung == "openrouter"


def test_local_mode_forces_the_coder_with_no_retry():
    d = policy.decide(verdict(0.05, 3.0), AVAILABLE, cfg(), mode="local")
    assert d.rung == "coder" and d.chain == ["coder"]


def test_local_mode_stays_local_while_claude_is_locked_out():
    d = policy.decide(verdict(0.05, 3.0), LOCKED_OUT, cfg(), mode="local")
    assert d.rung == "coder" and d.chain == ["coder"]


def test_claude_mode_forces_claude_with_its_retry():
    d = policy.decide(verdict(0.99, 0.0), AVAILABLE, cfg(), mode="claude")
    assert d.rung == "claude" and d.chain == ["claude", "claude"]


def test_claude_mode_while_locked_out_hands_back_with_the_reset():
    d = policy.decide(verdict(0.5, 1.0), LOCKED_OUT, cfg(), mode="claude")
    assert d.rung == policy.HAND_BACK and d.chain == []
    assert "Mon 28 Sep 09:00" in d.reason


def test_paid_rung_is_claude_until_locked_out():
    assert policy.paid_rung(AVAILABLE, cfg())[0] == "claude"
    assert policy.paid_rung(LOCKED_OUT, cfg())[0] == "openrouter"


def test_limit_hit_makes_claude_unavailable_until_it_lapses(tmp_path):
    led = Ledger(tmp_path / "l.db")
    led.add_limit_hit(at(2026, 9, 28, 9))
    assert not policy.claude_state(led, at(2026, 9, 26)).available
    assert policy.claude_state(led, at(2026, 9, 28, 10)).available


def test_old_ledger_gains_claude_column_and_keeps_rows(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, ts REAL NOT NULL, brief TEXT NOT NULL, workdir TEXT,"
                " rung TEXT NOT NULL, reason TEXT NOT NULL, verdict TEXT, pace TEXT)")
    old.execute("INSERT INTO decisions VALUES ('a', 1, 'b', NULL, 'coder', 'r', NULL, '{}')")
    old.execute("CREATE TABLE limit_hits (ts REAL NOT NULL, window_start REAL NOT NULL, spent REAL NOT NULL,"
                " until REAL NOT NULL)")
    old.execute("INSERT INTO limit_hits VALUES (1, 0, 5, 2)")
    old.commit()
    old.close()
    led = Ledger(path)
    new_id = led.add_decision("brief", None, "claude", "why", None, {"available": True})
    assert led.decision("a")["rung"] == "coder"
    assert json.loads(led.decision(new_id)["claude"]) == {"available": True}
    led.add_limit_hit(3, raw="limit", reset_source="fallback")
    assert led.decision(new_id)["commit_sha"] is None
    rows = led.db.execute("SELECT until, reset_source, raw FROM limit_hits ORDER BY ts").fetchall()
    assert [tuple(r) for r in rows] == [(2, None, None), (3, "fallback", "limit")]


# -- backends ----------------------------------------------------------------

def test_parse_detects_limit_hit():
    out = json.dumps({"type": "result", "is_error": True, "result": "Claude usage limit reached. Your limit resets at 9am"})
    r = backends.parse("claude", out, "", 1, 3.0)
    assert r.limit_hit and not r.ok


THU_NOON = at(2026, 9, 24, 12)


@pytest.mark.parametrize("text, expected", [
    # Wordings are invented but plausible; replace with real ones from the ledger's raw column.
    ("Session limit reached ∙ resets 3pm", at(2026, 9, 24, 15)),
    ("You've hit your limit · resets at 9am", at(2026, 9, 25, 9)),
    ("Limit reached, resets at 17:30", datetime(2026, 9, 24, 17, 30, tzinfo=ZoneInfo(TZ)).timestamp()),
    ("You've hit your weekly usage limit. Resets Monday 9am", at(2026, 9, 28, 9)),
    ("Weekly limit reached ∙ resets Sep 28, 1pm", at(2026, 9, 28, 13)),
    ("Usage limit reached. Resets in 2h 30m", THU_NOON + 2.5 * 3600),
    ("5-hour limit reached, resets in 45 minutes", THU_NOON + 45 * 60),
    ("Claude AI usage limit reached|1790260000", 1790260000.0),
])
def test_reset_time_reads_clock_date_and_relative_forms(text, expected):
    assert backends.reset_time(text, THU_NOON, TZ) == pytest.approx(expected)


def test_reset_time_honours_a_timezone_named_in_the_error():
    got = backends.reset_time("Session limit reached ∙ resets 3pm (UTC)", THU_NOON, TZ)
    assert got == datetime(2026, 9, 24, 15, tzinfo=ZoneInfo("UTC")).timestamp()


@pytest.mark.parametrize("text", [
    "Usage limit reached.",
    "Claude AI usage limit reached|1000000000",      # in the past
    "Weekly limit reached ∙ resets Dec 24, 1pm",     # months away: not a limit reset
    "limit reached, resets 3",                       # no minutes or am/pm: too ambiguous
])
def test_reset_time_unreadable_is_none(text):
    assert backends.reset_time(text, THU_NOON, TZ) is None


def test_parse_success_reads_cost_and_turns():
    out = json.dumps({"is_error": False, "result": "done", "total_cost_usd": 1.25, "num_turns": 7, "session_id": "s"})
    r = backends.parse("claude", out, "", 0, 3.0)
    assert r.ok and r.cost == 1.25 and r.turns == 7 and not r.limit_hit


def test_subscription_env_strips_api_billing_vars_but_keeps_the_login_dir():
    env = backends.environment("claude", {"ANTHROPIC_API_KEY": "sk", "PATH": "/bin", "CLAUDE_CONFIG_DIR": "/c"})
    assert "ANTHROPIC_API_KEY" not in env and env["PATH"] == "/bin" and env["CLAUDE_CONFIG_DIR"] == "/c"
    assert backends.CHILD_ENV not in env


def test_openrouter_command_runs_this_profile_one_shot(tmp_path):
    cmd = backends.command(cfg(), "openrouter", "do it", usage_file=tmp_path / "u.json", workdir=tmp_path)
    assert cmd == ["locoder", "-z", backends.CHILD_PREAMBLE + "do it", "-m", "deepseek/deepseek-v4.1-flash",
                   "--provider", "custom:openrouter", "--in", str(tmp_path),
                   "--usage-file", str(tmp_path / "u.json"), "-t", "file,terminal,web,todo"]
    assert "claude" not in cmd


def test_openrouter_env_keeps_the_profile_env_and_marks_the_child():
    env = backends.environment("openrouter", {"OPENROUTER_API_KEY": "or-key", "HERMES_HOME": "/h"})
    assert env == {"OPENROUTER_API_KEY": "or-key", "HERMES_HOME": "/h", backends.CHILD_ENV: "1"}


def test_parse_oneshot_reads_usage_and_exit_code():
    usage = {"estimated_cost_usd": 0.12, "cost_status": "estimated", "api_calls": 5, "session_id": "s"}
    r = backends.parse_oneshot("all done\n", "", 0, 3.0, usage)
    assert r.ok and r.cost == 0.12 and r.turns == 5 and r.result == "all done" and r.cost_status == "estimated"
    failed = backends.parse_oneshot("", "boom", 2, 3.0, None)
    assert not failed.ok and failed.cost == 0.0 and failed.result == "boom" and not failed.limit_hit


def test_parse_oneshot_reads_a_real_failed_usage_file():
    # What the pinned Hermes wrote when its client failed to start: every field null.
    usage = {k: None for k in ("estimated_cost_usd", "cost_status", "api_calls", "session_id", "completed")}
    usage.update(failed=True, failure="Failed to initialize OpenAI client")
    r = backends.parse_oneshot("", "hermes -z: agent failed: Failed to initialize OpenAI client", 1, 2.0, usage)
    assert not r.ok and r.cost == 0.0 and r.turns is None and "agent failed" in r.result


def test_workdir_outside_roots_is_refused(tmp_path):
    (tmp_path / "ok").mkdir()
    assert backends.check_workdir(str(tmp_path / "ok"), [str(tmp_path)])
    with pytest.raises(backends.BackendError):
        backends.check_workdir("/", [str(tmp_path)])


# -- tools end to end --------------------------------------------------------

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
mode = open(os.environ["FAKE_CLAUDE_MODE"]).read().strip()
if mode == "limit":
    print(json.dumps({"is_error": True, "result": "You've hit your weekly usage limit. Resets Monday 9am"}))
    sys.exit(1)
if mode == "limit-3h":
    print(json.dumps({"is_error": True, "result": "Session limit reached. Resets in 3h"}))
    sys.exit(1)
if mode == "limit-no-reset":
    print(json.dumps({"is_error": True, "result": "Usage limit reached."}))
    sys.exit(1)
print(json.dumps({"is_error": False, "result": "done via max", "total_cost_usd": 2.5, "num_turns": 9}))
"""


FAKE_HERMES = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
usage = argv[argv.index("--usage-file") + 1]
open(os.environ["FAKE_HERMES_ARGV"], "w").write(json.dumps(argv))
fail = open(os.environ["FAKE_HERMES_MODE"]).read().strip() == "fail"
json.dump({"estimated_cost_usd": 0.4, "cost_status": "estimated", "api_calls": 3, "session_id": "h1",
           "completed": not fail}, open(usage, "w"))
print("gave up" if fail else "done via openrouter")
sys.exit(2 if fail else 0)
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
    hermes = tmp_path / "locoder"
    hermes.write_text(FAKE_HERMES)
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    mode = tmp_path / "mode"
    mode.write_text("ok")
    hermes_mode = tmp_path / "hermes-mode"
    hermes_mode.write_text("ok")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", str(mode))
    monkeypatch.setenv("FAKE_HERMES_MODE", str(hermes_mode))
    monkeypatch.setenv("FAKE_HERMES_ARGV", str(tmp_path / "hermes-argv"))
    (tmp_path / "proj").mkdir()
    c = cfg(claude={"bin": str(claude), "workdir_roots": [str(tmp_path)]},
            openrouter={"hermes_bin": str(hermes)}, graft={"enabled": False})
    state = {"verdict": verdict(0.9, 0.5), "now": at(2026, 9, 24), "roots": {}}
    r = Router(clock=lambda: state["now"], judge_factory=lambda _c: FakeJudge(state["verdict"]),
               ledger=Ledger(tmp_path / "ledger.db"), config_loader=lambda: c,
               conversation_root=lambda sid: state["roots"].get(sid, sid))
    r.test = {"mode": mode, "hermes_mode": hermes_mode, "argv": tmp_path / "hermes-argv",
              "proj": str(tmp_path / "proj"), "state": state}
    return r


def call(fn, session=None, **args):
    out = fn(args, **({"session_id": session} if session else {}))
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
    assert r["ok"] and r["backend"] == "claude" and r["run"]["cost"] == 2.5
    assert router.ledger.claude_spent(0) == 2.5


def test_limit_hit_mid_run_hands_back_the_locked_out_chain(router):
    router.test["state"]["verdict"] = verdict(0.4, 1.8)
    d = call(router.route, brief="medium task", workdir=router.test["proj"])
    assert d["rung"] == "claude"
    router.test["mode"].write_text("limit")
    r = call(router.escalate, brief="medium task", workdir=router.test["proj"], decision_id=d["decision_id"])
    assert not r["ok"] and r["backend"] == "claude"
    assert r["chain"] == ["coder", "openrouter"]
    assert r["claude_unavailable_until"] == "Mon 28 Sep 09:00"
    assert router.ledger.db.execute("SELECT COUNT(*) FROM attempts WHERE rung='openrouter'").fetchone()[0] == 0


def test_lockout_lasts_until_the_stated_reset_and_no_longer(router):
    router.test["mode"].write_text("limit")
    call(router.escalate, brief="task", workdir=router.test["proj"])
    router.test["state"]["verdict"] = verdict(0.1, 2.9)
    assert call(router.route, brief="hard task")["rung"] == "openrouter"
    assert call(router.status)["claude"]["available"] is False
    router.test["state"]["now"] = at(2026, 9, 28, 9) + 60
    assert call(router.route, brief="hard task")["rung"] == "claude"


def test_escalate_without_a_decision_treats_the_rest_as_pessimistic(router):
    router.test["mode"].write_text("limit")
    r = call(router.escalate, brief="task", workdir=router.test["proj"])
    assert r["chain"] == ["openrouter"]


def test_unreadable_reset_locks_out_for_the_fallback_period_and_keeps_the_text(router):
    router.test["mode"].write_text("limit-no-reset")
    call(router.escalate, brief="task", workdir=router.test["proj"])
    hit = router.ledger.db.execute("SELECT until, reset_source, raw FROM limit_hits").fetchone()
    assert hit["until"] == pytest.approx(at(2026, 9, 24) + 3600)
    assert hit["reset_source"] == "fallback" and hit["raw"] == "Usage limit reached."
    router.test["state"]["now"] = at(2026, 9, 24) + 3601
    assert call(router.status)["claude"]["available"] is True


def test_stated_reset_is_recorded_with_its_text(router):
    router.test["mode"].write_text("limit")
    call(router.escalate, brief="task", workdir=router.test["proj"])
    hit = router.ledger.db.execute("SELECT reset_source, raw FROM limit_hits").fetchone()
    assert hit["reset_source"] == "stated" and "Resets Monday 9am" in hit["raw"]


def test_route_with_judge_down_still_decides(router):
    router.test["state"]["verdict"] = None
    d = call(router.route, brief="anything")
    assert d["rung"] == "claude" and d["judge"] is None


def _git(repo, *argv):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *argv],
                   check=True, capture_output=True)


def test_route_records_the_commit_and_dirty_state_of_a_git_workdir(router, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "a")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    clean = router.ledger.decision(call(router.route, brief="t", workdir=str(repo))["decision_id"])
    assert clean["commit_sha"] == sha and clean["dirty"] == 0
    (repo / "test_new.py").write_text("untracked failing test")
    dirty = router.ledger.decision(call(router.route, brief="t", workdir=str(repo))["decision_id"])
    assert dirty["commit_sha"] == sha and dirty["dirty"] == 1


def test_route_without_git_records_no_commit(router):
    for workdir in (router.test["proj"], None):
        row = router.ledger.decision(call(router.route, brief="t", workdir=workdir)["decision_id"])
        assert row["commit_sha"] is None and row["dirty"] is None


def test_route_records_claude_availability_on_the_decision(router):
    d = call(router.route, brief="small fix")
    assert d["claude"]["available"] is True
    assert json.loads(router.ledger.decision(d["decision_id"])["claude"])["available"] is True


def test_openrouter_rung_runs_hermes_one_shot_and_records_its_cost(router):
    d = call(router.route, brief="task", workdir=router.test["proj"])
    r = call(router.escalate, brief="task", workdir=router.test["proj"], decision_id=d["decision_id"],
             backend="openrouter")
    assert r["ok"] and r["backend"] == "openrouter" and r["run"]["result"] == "done via openrouter"
    assert r["run"]["cost_status"] == "estimated"
    argv = json.loads(router.test["argv"].read_text())
    assert argv[0] == "-z" and argv[1].endswith("\n\ntask") and argv[argv.index("--in") + 1] == router.test["proj"]
    assert router.ledger.stats(0)["openrouter"]["cost"] == 0.4


def test_failed_openrouter_run_still_records_its_cost(router):
    router.test["hermes_mode"].write_text("fail")
    r = call(router.escalate, brief="task", workdir=router.test["proj"], backend="openrouter")
    assert not r["ok"] and r["run"]["result"] == "gave up"
    assert router.ledger.stats(0)["openrouter"]["cost"] == 0.4


def test_auto_goes_to_openrouter_while_claude_is_locked_out(router):
    router.test["mode"].write_text("limit")
    call(router.escalate, brief="task", workdir=router.test["proj"])
    r = call(router.escalate, brief="task", workdir=router.test["proj"])
    assert r["backend"] == "openrouter" and r["ok"]


def test_escalate_rejects_workdir_outside_roots(router):
    assert "outside the allowed roots" in call(router.escalate, brief="x", workdir="/")["error"]


def test_register_puts_all_tools_in_coding_toolset():
    seen = []

    class Ctx:
        def register_tool(self, **kw):
            seen.append((kw["name"], kw["toolset"], kw.get("is_async", False)))

    register(Ctx(), router=object.__new__(Router))
    assert seen == [("route", "coding", False), ("escalate", "coding", True),
                    ("route_outcome", "coding", False), ("routing_status", "coding", False),
                    ("routing_mode", "coding", False)]


def test_settings_merge_routing_yaml(tmp_path):
    f = tmp_path / "routing.yaml"
    f.write_text("policy:\n  local_threshold: 0.8\nopenrouter:\n  enabled: false\n")
    c = settings.load(f)
    assert c["policy"]["local_threshold"] == 0.8 and c["policy"]["fallback_threshold"] == 0.3
    assert c["openrouter"]["enabled"] is False


# -- routing modes -----------------------------------------------------------

def test_sessions_start_in_auto_and_switch_independently(router):
    router.test["state"]["verdict"] = verdict(0.1, 2.9)
    assert call(router.set_mode, session="s1")["mode"] == "auto"
    assert call(router.set_mode, session="s1", mode="local")["mode"] == "local"
    d = call(router.route, session="s1", brief="hard task")
    assert d["rung"] == "coder" and d["chain"] == ["coder"] and d["mode"] == "local"
    assert call(router.route, session="s2", brief="hard task")["rung"] == "claude"
    assert call(router.status, session="s1")["mode"] == "local"
    assert call(router.status, session="s2")["mode"] == "auto"


def test_forced_decisions_still_record_the_verdict_and_the_mode(router):
    call(router.set_mode, session="s1", mode="local")
    d = call(router.route, session="s1", brief="task")
    row = router.ledger.decision(d["decision_id"])
    assert row["mode"] == "local" and json.loads(row["verdict"])["p_local"] == 0.9


def test_claude_mode_hands_back_while_locked_out(router):
    call(router.set_mode, session="s1", mode="claude")
    router.test["mode"].write_text("limit")
    r = call(router.escalate, session="s1", brief="task", workdir=router.test["proj"])
    assert r["chain"] == [] and "tell the user" in r["next"]
    assert r["claude_unavailable_until"] == "Mon 28 Sep 09:00"
    d = call(router.route, session="s1", brief="task")
    assert d["rung"] == "user" and d["chain"] == [] and "stop" in d["next"]


def test_unknown_mode_is_refused(router):
    assert "mode must be one of" in call(router.set_mode, session="s1", mode="cheap")["error"]


def test_mode_survives_compression_rotating_the_session_id(router):
    call(router.set_mode, session="s1", mode="local")
    router.test["state"]["roots"]["s1-rotated"] = "s1"
    assert call(router.status, session="s1-rotated")["mode"] == "local"
    assert call(router.status, session="s9")["mode"] == "auto"


def test_escalate_refuses_paid_rungs_in_local_mode(router):
    call(router.set_mode, session="s1", mode="local")
    r = call(router.escalate, session="s1", brief="task", workdir=router.test["proj"])
    assert "paid rungs are off" in r["error"]


def test_escalate_in_claude_mode_never_runs_openrouter(router):
    call(router.set_mode, session="s1", mode="claude")
    assert "OpenRouter is off" in call(router.escalate, session="s1", brief="t", workdir=router.test["proj"],
                                       backend="openrouter")["error"]
    router.test["mode"].write_text("limit")
    call(router.escalate, session="s1", brief="t", workdir=router.test["proj"])
    r = call(router.escalate, session="s1", brief="t", workdir=router.test["proj"])
    assert r["chain"] == [] and r["backend"] is None and r["claude_unavailable_until"] == "Mon 28 Sep 09:00"
    assert router.ledger.db.execute("SELECT COUNT(*) FROM attempts WHERE rung='openrouter'").fetchone()[0] == 0


def test_lockout_chain_skips_a_rung_this_task_already_failed(router):
    router.test["state"]["verdict"] = verdict(0.9, 0.3)
    d = call(router.route, brief="task", workdir=router.test["proj"])
    assert d["chain"] == ["coder", "claude", "claude"]
    call(router.outcome, decision_id=d["decision_id"], rung="coder", verified=False)
    router.test["mode"].write_text("limit")
    r = call(router.escalate, brief="task", workdir=router.test["proj"], decision_id=d["decision_id"])
    assert r["chain"] == ["openrouter"]


def test_routing_tools_refuse_inside_the_openrouter_child(router, monkeypatch):
    monkeypatch.setenv(backends.CHILD_ENV, "1")
    for fn, args in ((router.route, {"brief": "t"}), (router.escalate, {"brief": "t", "workdir": router.test["proj"]}),
                     (router.outcome, {"decision_id": "x", "rung": "coder", "verified": True}),
                     (router.set_mode, {"mode": "local"})):
        assert "delegated run" in call(fn, **args)["error"]


def test_routing_status_reports_this_weeks_results(router):
    d = call(router.route, brief="t")
    call(router.outcome, decision_id=d["decision_id"], rung="coder", verified=True)
    call(router.escalate, brief="t", workdir=router.test["proj"])
    week = call(router.status)["this_week"]
    assert week["coder"] == {"attempts": 1, "verified": 1, "failed": 0, "cost": 0}
    assert week["claude"]["attempts"] == 1 and week["claude"]["cost"] == 2.5


def test_a_session_limit_locks_out_for_hours_not_the_week(router):
    start = router.test["state"]["now"]
    router.test["mode"].write_text("limit-3h")
    call(router.escalate, brief="t", workdir=router.test["proj"])
    router.test["state"]["verdict"] = verdict(0.1, 2.9)
    router.test["state"]["now"] = start + 3 * 3600 - 60
    assert call(router.route, brief="hard")["rung"] == "openrouter"
    router.test["state"]["now"] = start + 3 * 3600 + 1
    assert call(router.route, brief="hard")["rung"] == "claude"
