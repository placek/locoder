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
so asking never evicts the orchestrator's or the coder's single KV slot. A Jev or CLM backend
would be another class with the same method.

Every answer carries *coverage*: the share of the model's probability mass that landed on an
allowed answer. The distribution is renormalised over the allowed answers, so coverage is the
only sign that the model mostly wanted to say something else.
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


# -- the routing judge ----------------------------------------------------------------

# Who the questions are about: the coder preset. Keep in step with llama/presets.ini.
WORKER = ("a small local coding model (3B active parameters, 128k context, can read files, "
          "edit, run tests, at most 60 steps, no human help)")

QUESTIONS: Dict[str, Question] = {
    "difficulty": Score(
        f"The task below would be done by {WORKER}.\nHow hard is it for that model?",
        {0: "trivial: one small, obvious edit",
         1: "routine: a contained change in one module, clear acceptance check",
         2: "demanding: several files, design judgement, or unfamiliar APIs",
         3: "heavy: a large feature, a wide refactor, or a deep debugging trace"}),
    "local": Noul(
        f"The task below would be done by {WORKER}.\n"
        "Will that model finish it correctly, with its acceptance check passing?"),
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


__all__: List[str] = ["Answer", "Backend", "Choice", "Judge", "JudgeError", "Noul", "QUESTIONS",
                      "Score", "SemIfBackend", "Verdict", "distribution", "render", "verdict_dict"]
