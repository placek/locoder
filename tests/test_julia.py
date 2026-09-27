"""The Julia-1 shadow judge: the sidecar's HTTP handler, the backend that talks to it, how route()
records it without acting on it, and how `make report` scores it against the real judge."""
import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from routing import settings
from routing.judge import (QUESTIONS, Choice, Judge, JudgeError, JuliaBackend, Noul, Score, Verdict,
                           julia_question)
from routing.ledger import Ledger
from routing.tools import Router, shadow_judge

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = load("julia_server", ROOT / "julia" / "server.py")
report = load("report_for_julia", ROOT / "scripts" / "report.py")


def fake_predict(state, questions):
    """Julia-1's typed API as julia.typed.predict_typed behaves (and julia-mlx mirrors): option keys
    false/true for noul, "0".."n-1" for score, the criteria's names for choice."""
    if len(str(state)) > 2000:
        raise ValueError("state exceeds lossless context budget")
    answers = {}
    for name, q in questions.items():
        kind, criteria = q["type"], q.get("criteria")
        if kind == "noul":
            if criteria is not None and set(criteria) != {"false", "true"}:
                raise ValueError("noul criteria must map false and true to descriptions")
            answers[name] = {"type": "noul", "probabilities": {"false": 0.25, "true": 0.75}, "noul": 0.75}
        elif kind == "score":
            n = len(criteria)
            probs = {str(i): (0.5 if i == 1 else 0.5 / (n - 1)) for i in range(n)}
            answers[name] = {"type": "score", "probabilities": probs,
                             "score": sum(i * p for i, p in enumerate(probs.values()))}
        else:
            keys = list(criteria)
            answers[name] = {"type": "choice", "probabilities": {k: 1 / len(keys) for k in keys}, "choice": keys[0]}
    return {"answers": answers}


@pytest.fixture
def julia():
    calls = []

    def predict(state, questions):
        calls.append({"state": state, "questions": questions})
        return fake_predict(state, questions)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(predict, {"repo": "SupersonicLabs/Julia-1",
                                                                              "commit": "abc123"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{httpd.server_port}", "calls": calls}
    httpd.shutdown()


# -- the questions on the wire -------------------------------------------------------------

def test_questions_become_julias_typed_questions():
    wire, read = julia_question(Noul("Done?", {"yes": "it passes", "no": "it fails"}))
    assert wire == {"type": "noul", "instructions": "Done?", "criteria": {"false": "it fails", "true": "it passes"}}
    assert read({"probabilities": {"false": 0.2, "true": 0.8}}).probs == {"no": 0.2, "yes": 0.8}
    assert "criteria" not in julia_question(Noul("Done?"))[0]

    wire, read = julia_question(Score("How hard?", {1: "easy", 3: "hard", 2: "fair"}))
    assert wire["criteria"] == ["easy", "fair", "hard"]            # rubric order, by level
    assert read({"probabilities": {"0": 0.1, "1": 0.3, "2": 0.6}}).probs == {"1": 0.1, "2": 0.3, "3": 0.6}

    wire, read = julia_question(Choice("Team?", {"billing": "charges", "support": "the rest"}))
    assert wire["criteria"] == {"billing": "charges", "support": "the rest"}
    assert read({"probabilities": {"billing": 1.0, "support": 0.0}}).coverage == 1.0


def test_julia_limits_are_refused_before_asking():
    with pytest.raises(ValueError):
        julia_question(Score("x", {0: "only one level"}))
    with pytest.raises(ValueError):
        julia_question(Choice("x", {f"o{i}": "d" for i in range(21)}))


@pytest.mark.parametrize("answer", [None, {}, {"probabilities": {"true": 1.0}},
                                    {"probabilities": {"false": 0.5, "true": 0.9}},
                                    {"probabilities": {"false": "a", "true": 0.5}}])
def test_malformed_answers_are_judge_errors(answer):
    _, read = julia_question(Noul("Done?"))
    with pytest.raises(JudgeError):
        read(answer)


# -- the backend against the sidecar's own handler -------------------------------------------

def test_routing_judge_through_the_sidecar(julia):
    v = Judge(JuliaBackend(julia["url"])).judge("Goal: fix a typo\nAcceptance: grep -q x README.md")
    assert v.p_local == pytest.approx(0.75)
    assert v.expected_difficulty == pytest.approx(0 * 0.5 / 3 + 1 * 0.5 + 2 * 0.5 / 3 + 3 * 0.5 / 3)
    assert v.coverage == 1.0
    [call] = julia["calls"]
    assert call["questions"]["local"]["criteria"]["true"] == QUESTIONS["local"].criteria["yes"]
    assert call["questions"]["difficulty"]["criteria"][0].startswith("trivial")


def test_an_overlong_brief_is_refused_with_julias_reason(julia):
    with pytest.raises(JudgeError, match="lossless context budget"):
        Judge(JuliaBackend(julia["url"])).judge("x" * 5000)


def test_unreachable_sidecar_is_a_judge_error():
    with pytest.raises(JudgeError):
        Judge(JuliaBackend("http://127.0.0.1:9", timeout_s=1)).judge("x")


def test_the_handler_rejects_what_is_not_a_typed_request(julia):
    import urllib.error
    import urllib.request

    def post(body):
        req = urllib.request.Request(julia["url"] + "/predict", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    assert post(b"not json")[0] == 400
    assert post(json.dumps({"questions": {}}).encode())[0] == 400
    code, body = post(json.dumps({"state": "s", "questions": {"q": {"type": "noul", "criteria": {"yes": "y"}}}}).encode())
    assert code == 400 and "noul criteria" in body["error"]
    with urllib.request.urlopen(julia["url"] + "/health", timeout=5) as resp:
        assert json.loads(resp.read())["commit"] == "abc123"


def test_a_crashing_model_is_a_500_not_a_dead_server():
    def boom(state, questions):
        raise RuntimeError("tensor on fire")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(boom, {}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_port}"
        with pytest.raises(JudgeError, match="500.*tensor on fire"):
            JuliaBackend(url).system_one("s", {"q": Noul("x")})
        with pytest.raises(JudgeError, match="500"):                  # still serving
            JuliaBackend(url).system_one("s", {"q": Noul("x")})
    finally:
        httpd.shutdown()


# -- route() records the shadow and never acts on it ----------------------------------------

class Fixed:
    def __init__(self, v):
        self.v = v

    def judge(self, brief):
        if isinstance(self.v, Exception):
            raise self.v
        return self.v


def make_router(tmp_path, shadow, **over):
    c = settings._merge(settings.DEFAULTS, {"graft": {"enabled": False}, **over})
    return Router(judge_factory=lambda _c: Fixed(Verdict(0.9, {}, 0.3, 1.0)), ledger=Ledger(tmp_path / "l.db"),
                  config_loader=lambda: c, conversation_root=lambda s: s, shadow_factory=lambda _c: Fixed(shadow))


def routed(router):
    out = json.loads(router.route({"brief": "fix a typo"}))
    return out, router.ledger.decision(out["decision_id"])


def test_the_shadow_verdict_is_recorded_beside_the_judge(tmp_path):
    out, row = routed(make_router(tmp_path, Verdict(0.1, {"3": 1.0}, 3.0, 1.0)))
    assert out["rung"] == "coder" and "shadow" not in out          # decided by the judge alone
    shadow = json.loads(row["shadow"])
    assert shadow["backend"] == "julia" and shadow["verdict"]["p_local"] == 0.1 and "ms" in shadow
    assert json.loads(row["verdict"])["p_local"] == 0.9


@pytest.mark.parametrize("failure", [JudgeError("julia request failed: refused"), RuntimeError("anything at all")])
def test_a_failing_shadow_is_recorded_and_routing_goes_on(tmp_path, failure):
    out, row = routed(make_router(tmp_path, failure))
    assert out["rung"] == "coder"
    shadow = json.loads(row["shadow"])
    assert "verdict" not in shadow and type(failure).__name__ in shadow["error"]


def test_a_disabled_shadow_is_not_asked(tmp_path):
    router = make_router(tmp_path, RuntimeError("must not be called"), shadow_judge={"enabled": False})
    _, row = routed(router)
    assert row["shadow"] is None


def test_an_unknown_shadow_backend_is_an_error_not_a_guess():
    with pytest.raises(JudgeError, match="unknown shadow_judge.backend"):
        shadow_judge(settings._merge(settings.DEFAULTS, {"shadow_judge": {"backend": "jev"}}))


def test_older_ledgers_gain_the_shadow_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, ts REAL NOT NULL, brief TEXT NOT NULL, workdir TEXT,"
               " rung TEXT NOT NULL, reason TEXT NOT NULL, verdict TEXT)")
    db.execute("INSERT INTO decisions VALUES ('old', 0, 'b', NULL, 'claude', 'r', NULL)")
    db.commit()
    db.close()
    led = Ledger(path)
    assert led.decision("old")["shadow"] is None
    assert led.decision(led.add_decision("b", None, "claude", "r", None, None, shadow={"error": "x"}))["shadow"]


# -- make report compares the two ---------------------------------------------------------

def test_judge_scores():
    s = report.judge_scores([({"p_local": 1.0, "expected_difficulty": 0.0}, True),
                             ({"p_local": 0.5, "expected_difficulty": 2.0}, False)], 0.85, 0.5)
    assert s["brier"] == pytest.approx((0 + 0.25) / 2)
    assert s["picks"] == 1 and s["picks_passed"] == 1


def test_report_scores_the_shadow_against_the_judge(tmp_path):
    led = Ledger(tmp_path / "l.db")
    db = led.db

    def task(id_, judge_p, shadow, verified):
        db.execute("INSERT INTO decisions (id, ts, brief, rung, reason, verdict, shadow) VALUES (?,?,?,?,?,?,?)",
                   (id_, 0, "b", "coder", "r", json.dumps({"p_local": judge_p, "expected_difficulty": 0.2}),
                    json.dumps(shadow)))
        if verified is not None:
            db.execute("INSERT INTO attempts (decision_id, ts, rung, verified) VALUES (?,?,?,?)",
                       (id_, 1, "coder", verified))

    ok = lambda p: {"backend": "julia", "verdict": {"p_local": p, "expected_difficulty": 0.2}, "ms": 40}  # noqa: E731
    task("a", 0.9, ok(0.95), 1)
    task("b", 0.9, ok(0.10), 0)    # the judge was sure and wrong; the shadow saw it
    task("c", 0.9, ok(0.90), 1)
    task("d", 0.9, {"backend": "julia", "error": "JudgeError: julia request failed", "ms": 1}, 1)
    task("e", 0.9, ok(0.50), None)  # not labelled
    db.commit()
    c = settings._merge(settings.DEFAULTS, {})
    c["claude"]["week"] = {"reset_weekday": 0, "reset_hour": 9, "timezone": "Europe/Warsaw"}
    out = report.render(db, c, 10)
    assert "shadow judge (julia): answered 4 of 5 decisions, median 40 ms; errors: JudgeError ×1" in out
    assert "scored against 3 labelled coder attempts that both judges answered" in out
    lines = {line.split()[0]: line for line in out.splitlines() if "Brier" in line}
    assert "near-certain picks   3, passed 67%" in lines["judge"]
    assert "near-certain picks   2, passed 100%" in lines["julia"]
    brier = {k: float(v.split("Brier ")[1].split()[0]) for k, v in lines.items()}
    assert brier["julia"] < brier["judge"]


def test_self_test_drives_the_upstream_runtime_as_its_parity_tests_do(tmp_path):
    """server.py --self-test (run at image build) against stand-ins for torch and the model repo's
    julia package, called the way julia-mlx's parity tests call the real ones."""
    import subprocess
    import sys

    (tmp_path / "stub").mkdir()
    (tmp_path / "stub" / "torch.py").write_text(
        "_n = 1\ndef set_num_threads(n):\n    global _n; _n = n\ndef get_num_threads():\n    return _n\n")
    pkg = tmp_path / "model" / "julia"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "inference.py").write_text(
        "CALLS = []\n"
        "def load_model(path, device, max_length, head_length, strict_encoding):\n"
        "    CALLS.append((path, device, max_length, head_length, strict_encoding))\n"
        "    return 'engine'\n")
    (pkg / "typed.py").write_text(
        "import json, sys\nsys.path.insert(0, %r)\nfrom test_julia import fake_predict\n"
        "def predict_typed(engine, state, questions):\n"
        "    assert engine == 'engine'\n"
        "    from julia.inference import CALLS\n"
        "    assert CALLS[0][1:] == ('cpu', 1024, 512, True), CALLS\n"
        "    return fake_predict(state, questions)\n" % str(ROOT / "tests"))
    (tmp_path / "model" / "locoder-build.json").write_text(json.dumps({"commit": "abc123"}))
    env = {"PATH": "/usr/bin:/bin", "JULIA_CHECKPOINT": str(tmp_path / "model"), "JULIA_THREADS": "3",
           "PYTHONPATH": f"{tmp_path / 'stub'}:{ROOT / 'plugins' / 'locoder'}"}
    out = subprocess.run([sys.executable, str(ROOT / "julia" / "server.py"), "--self-test"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["info"] == {"commit": "abc123", "max_length": 1024, "head_length": 512,
                              "strict_encoding": True, "threads": 3}
    assert set(result["answers"]) == {"local", "difficulty", "kind"}


@pytest.mark.parametrize("weights,ok", [(b"the real weights", True), (b"re-uploaded weights", False)])
def test_fetch_pins_the_weights_by_hash(tmp_path, weights, ok):
    import hashlib
    import subprocess
    import sys

    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "huggingface_hub.py").write_text(
        "import os, pathlib, types\n"
        "class HfApi:\n"
        "    def model_info(self, repo, revision):\n"
        "        return types.SimpleNamespace(sha='c0ffee' if revision == 'main' else revision)\n"
        "def snapshot_download(repo, revision, local_dir):\n"
        "    assert revision == 'c0ffee'\n"
        "    pathlib.Path(local_dir).mkdir(parents=True, exist_ok=True)\n"
        f"    pathlib.Path(local_dir, 'model.safetensors').write_bytes({weights!r})\n")
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(stub), "JULIA_CHECKPOINT": str(tmp_path / "model"),
           "JULIA_REPO": "SupersonicLabs/Julia-1", "JULIA_REVISION": "main",
           "JULIA_WEIGHTS_SHA256": hashlib.sha256(b"the real weights").hexdigest().upper()}
    out = subprocess.run([sys.executable, str(ROOT / "julia" / "fetch.py")], capture_output=True, text=True,
                         env=env, timeout=60)
    build = tmp_path / "model" / "locoder-build.json"
    if ok:
        assert out.returncode == 0, out.stderr
        assert json.loads(build.read_text())["commit"] == "c0ffee"
    else:
        assert out.returncode != 0 and "Upstream changed the weights" in out.stderr and not build.exists()
