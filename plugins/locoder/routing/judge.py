"""The judge: typed questions about a brief, answered as probabilities instead of text.

The interface is the one "System One" decision models share — TypeSafe's Jev and the open
CLM-8B: a *state* (the text being judged) plus typed questions, each answered with a
distribution rather than prose.

- ``Noul``   — is this statement true?            → P(yes)
- ``Choice`` — which of these named options?      → a distribution over the names
- ``Score``  — where on this 0..9 rubric?         → a distribution over the scores

A backend implements ``system_one(state, questions)``. The one here, ``SemIfBackend``, is the
SemIf trick on a small local model: each question becomes a prompt whose answer is a single
token, requested with ``max_tokens=1`` and ``top_logprobs``, and the probability of each
allowed token is read off. It runs on the ``judge`` preset of the llama.cpp router, CPU-only,
so asking never evicts the orchestrator's single KV slot. ``JuliaBackend`` asks Supersonic
Labs' Julia-1, a 144M-parameter decision model built for exactly this interface, served on the
CPU by ``julia/server.py``; route() runs it as a shadow, recorded but never acted on.

Every answer carries *coverage*: the share of the model's probability mass that landed on an
allowed answer. The distribution is renormalised over the allowed answers, so coverage is the
only sign that the model mostly wanted to say something else. A decision model like Julia-1
scores only the options it is given, so its coverage is 1 by construction.
"""
from __future__ import annotations

import json
import math
import string
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Protocol, Sequence, Tuple, Union


class JudgeError(RuntimeError):
    """The judge could not be asked or gave no usable distribution."""


# -- typed questions ----------------------------------------------------------------

@dataclass(frozen=True)
class Noul:
    instructions: str
    # What "yes" and "no" mean here. SemIf asks for Y/N and ignores it; Julia-1 scores the two
    # descriptions, which it does markedly better than the bare words.
    criteria: Optional[Mapping[str, str]] = None


@dataclass(frozen=True)
class Choice:
    instructions: str
    criteria: Mapping[str, str]          # option name -> what it means


@dataclass(frozen=True)
class Score:
    instructions: str
    scale: Mapping[int, str]             # 0..9 -> what the score means


Question = Union[Noul, Choice, Score]


@dataclass
class Answer:
    probs: Dict[str, float]              # Noul: "yes"/"no"; Choice: option names; Score: "0".."9"
    coverage: float

    @property
    def p_yes(self) -> float:
        return self.probs["yes"]

    @property
    def expected(self) -> float:
        return sum(int(k) * p for k, p in self.probs.items())


class Backend(Protocol):
    def system_one(self, state: str, questions: Mapping[str, Question]) -> Dict[str, Answer]: ...


# -- SemIf: one-token answers read from a local model's logprobs ----------------------

SYSTEM = ("You judge the text inside <state> and answer the question with exactly one "
          "character. No explanation.")


def distribution(top_logprobs: Sequence[dict], options: Sequence[str]) -> Answer:
    """Fold a top-k list into a distribution over *options* (single-token answers).

    Tokens are matched after stripping whitespace and case, so " Y", "y" and "Y" all count
    toward "Y" — tokenizers split the same answer in several ways.
    """
    wanted = {opt.casefold(): opt for opt in options}
    mass: Dict[str, float] = {opt: 0.0 for opt in options}
    total = 0.0
    for item in top_logprobs:
        try:
            p = math.exp(float(item["logprob"]))
        except (KeyError, TypeError, ValueError):
            continue
        total += p
        key = str(item.get("token", "")).strip().casefold()
        if key in wanted:
            mass[wanted[key]] += p
    matched = sum(mass.values())
    if matched <= 0.0:
        raise JudgeError(f"none of {list(options)} appeared in the judge's top tokens")
    return Answer(
        probs={opt: m / matched for opt, m in mass.items()},
        coverage=matched / total if total > 0 else 0.0,
    )


def render(question: Question) -> Tuple[str, Dict[str, str]]:
    """The prompt for *question*, and which answer token stands for which answer name."""
    if isinstance(question, Noul):
        return f"{question.instructions}\nAnswer Y or N.", {"Y": "yes", "N": "no"}
    if isinstance(question, Score):
        if not question.scale or any(not 0 <= k <= 9 for k in question.scale):
            raise ValueError("a Score scale is 0..9: one digit per score")
        lines = "\n".join(f"{k} = {v}" for k, v in sorted(question.scale.items()))
        return (f"{question.instructions}\n{lines}\nAnswer with one digit.",
                {str(k): str(k) for k in question.scale})
    if isinstance(question, Choice):
        if not 0 < len(question.criteria) <= 26:
            raise ValueError("a Choice has 1..26 options: one letter per option")
        letters = dict(zip(string.ascii_uppercase, question.criteria))
        lines = "\n".join(f"{letter} = {name}: {question.criteria[name]}" for letter, name in letters.items())
        return f"{question.instructions}\n{lines}\nAnswer with one letter.", letters
    raise TypeError(f"not a question: {question!r}")


class SemIfBackend:
    def __init__(self, base_url: str, model: str, timeout_s: float = 30, opener=None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout_s = timeout_s
        self._open = opener or urllib.request.urlopen

    def system_one(self, state: str, questions: Mapping[str, Question]) -> Dict[str, Answer]:
        return {name: self._ask(state, question) for name, question in questions.items()}

    def _ask(self, state: str, question: Question) -> Answer:
        prompt, tokens = render(question)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"{prompt}\n\n<state>\n{state.strip()}\n</state>"},
            ],
            "max_tokens": 1,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": 20,
            # Reasoning models would spend the single token on "<think>"; llama.cpp passes this
            # through to the chat template. Harmless for templates that ignore it.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._open(req, timeout=self.timeout_s) as resp:
                payload = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise JudgeError(f"judge request failed: {exc}") from exc
        try:
            top = payload["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        except (KeyError, IndexError, TypeError) as exc:
            raise JudgeError("judge response carried no logprobs; is the server llama.cpp?") from exc
        folded = distribution(top, list(tokens))
        return Answer(probs={tokens[t]: p for t, p in folded.probs.items()}, coverage=folded.coverage)


# -- Julia-1: a decision model that scores the options it is given --------------------

class JuliaBackend:
    """Julia-1's typed API over HTTP (``julia/server.py``): Noul -> noul, Score -> score, Choice ->
    choice, each answered with a probability per option. Julia-1 takes 2..20 options per question,
    a Score's levels are its rubric's positions (0..n-1), and its evaluated input is 1,024 tokens:
    the server refuses a longer brief instead of truncating it, and that is a JudgeError here."""

    def __init__(self, base_url: str, timeout_s: float = 5, opener=None):
        self.url = base_url.rstrip("/") + "/predict"
        self.timeout_s = timeout_s
        self._open = opener or urllib.request.urlopen

    def system_one(self, state: str, questions: Mapping[str, Question]) -> Dict[str, Answer]:
        wire, readers = {}, {}
        for name, question in questions.items():
            wire[name], readers[name] = julia_question(question)
        body = json.dumps({"state": state.strip(), "questions": wire}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with self._open(req, timeout=self.timeout_s) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                reason = json.loads(exc.read().decode()).get("error", "")
            except (ValueError, AttributeError, OSError):
                reason = ""
            raise JudgeError(f"julia refused the request ({exc.code}): {reason or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise JudgeError(f"julia request failed: {exc}") from exc
        answers = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            raise JudgeError("julia response carried no answers")
        return {name: readers[name](answers.get(name)) for name in questions}


def julia_question(question: Question):
    """The typed question Julia-1 expects, and how to read its answer back into ours."""
    if isinstance(question, Noul):
        wire = {"type": "noul", "instructions": question.instructions}
        if question.criteria:
            wire["criteria"] = {"false": question.criteria["no"], "true": question.criteria["yes"]}
        return wire, lambda a: _julia_answer(a, {"false": "no", "true": "yes"})
    if isinstance(question, Score):
        levels = sorted(question.scale)
        if not 2 <= len(levels) <= 20:
            raise ValueError("Julia-1 scores 2..20 levels")
        wire = {"type": "score", "instructions": question.instructions,
                "criteria": [question.scale[k] for k in levels]}
        return wire, lambda a: _julia_answer(a, {str(i): str(k) for i, k in enumerate(levels)})
    if isinstance(question, Choice):
        if not 2 <= len(question.criteria) <= 20:
            raise ValueError("Julia-1 chooses among 2..20 options")
        wire = {"type": "choice", "instructions": question.instructions, "criteria": dict(question.criteria)}
        return wire, lambda a: _julia_answer(a, {name: name for name in question.criteria})
    raise TypeError(f"not a question: {question!r}")


def _julia_answer(answer, names: Mapping[str, str]) -> Answer:
    """Julia's per-option probabilities, renamed to ours. Coverage is 1: it scores only these."""
    probs = answer.get("probabilities") if isinstance(answer, dict) else None
    if not isinstance(probs, dict) or set(probs) != set(names):
        raise JudgeError(f"julia answer lacks probabilities for {sorted(names)}: {answer!r}"[:300])
    try:
        values = {names[k]: float(v) for k, v in probs.items()}
    except (TypeError, ValueError) as exc:
        raise JudgeError(f"julia answer has non-numeric probabilities: {probs!r}"[:300]) from exc
    total = sum(values.values())
    if not all(math.isfinite(v) and v >= 0 for v in values.values()) or not 0.99 <= total <= 1.01:
        raise JudgeError(f"julia probabilities do not form a distribution: {probs!r}"[:300])
    return Answer(probs=values, coverage=1.0)


# -- the routing judge ----------------------------------------------------------------

# Who the questions are about: the model delegation.model names in profile/config.yaml, with
# its ctx-size from llama/presets.ini and delegation.max_iterations (tests/test_stack.py checks both).
WORKER = ("a local coding model (118B mixture-of-experts, 8B active parameters, 128k context, "
          "slow on this machine; can read files, edit, run tests; at most 60 steps; no human help)")

QUESTIONS: Dict[str, Question] = {
    "difficulty": Score(
        f"The task below would be done by {WORKER}.\nHow hard is it for that model?",
        {0: "trivial: one small, obvious edit",
         1: "routine: a contained change in one module, clear acceptance check",
         2: "demanding: several files, design judgement, or unfamiliar APIs",
         3: "heavy: a large feature, a wide refactor, or a deep debugging trace"}),
    "local": Noul(
        f"The task below would be done by {WORKER}.\n"
        "Will that model finish it correctly, with its acceptance check passing?",
        {"yes": "it finishes the task and the acceptance check passes",
         "no": "it fails, gives up or runs out of steps, and the acceptance check does not pass"}),
}


@dataclass
class Verdict:
    p_local: float
    difficulty: Dict[str, float]
    expected_difficulty: float
    coverage: float                      # the lower of the two answers' coverage
    raw: Dict[str, Answer] = field(default_factory=dict)


class Judge:
    def __init__(self, backend: Backend):
        self.backend = backend

    def judge(self, brief: str) -> Verdict:
        a = self.backend.system_one(brief, QUESTIONS)
        diff, local = a["difficulty"], a["local"]
        return Verdict(
            p_local=local.p_yes,
            difficulty=diff.probs,
            expected_difficulty=diff.expected,
            coverage=min(diff.coverage, local.coverage),
            raw=a,
        )


def verdict_dict(v: Optional[Verdict]) -> Optional[dict]:
    if v is None:
        return None
    return {
        "p_local": round(v.p_local, 3),
        "difficulty": {k: round(p, 3) for k, p in v.difficulty.items()},
        "expected_difficulty": round(v.expected_difficulty, 2),
        "coverage": round(v.coverage, 3),
    }


__all__: List[str] = ["Answer", "Backend", "Choice", "Judge", "JudgeError", "JuliaBackend", "Noul", "QUESTIONS",
                      "Score", "SemIfBackend", "Verdict", "distribution", "julia_question", "render",
                      "verdict_dict"]
