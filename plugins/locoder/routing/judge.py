"""The judge: two typed questions answered from next-token probabilities, not generated text.

Same trick as SemIf: ask a small local model a question whose answer is one token, request
``max_tokens=1`` with ``top_logprobs``, and read the probability of each allowed answer. One
prefill per question, no decoding, no parsing of prose. The model is the ``judge`` preset of
the llama.cpp router — a small CPU-only model, so asking never evicts the orchestrator's or
the coder's KV cache (both run with a single slot).
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

SYSTEM = (
    "You are the routing judge for a coding agent. You read a task brief and answer the "
    "question with exactly one character. No explanation."
)

DIFFICULTY_Q = (
    "The task below would be done by a small local coding model (3B active parameters, 128k "
    "context, can read files, edit, run tests, at most 60 steps, no human help).\n"
    "How hard is it for that model?\n"
    "0 = trivial: one small, obvious edit\n"
    "1 = routine: a contained change in one module, clear acceptance check\n"
    "2 = demanding: several files, design judgement, or unfamiliar APIs\n"
    "3 = heavy: a large feature, a wide refactor, or a deep debugging trace\n"
    "Answer with one digit."
)

LOCAL_Q = (
    "The task below would be done by a small local coding model (3B active parameters, 128k "
    "context, can read files, edit, run tests, at most 60 steps, no human help).\n"
    "Will that model finish it correctly, with its acceptance check passing?\n"
    "Answer Y or N."
)

DIFFICULTY_OPTIONS = ("0", "1", "2", "3")
LOCAL_OPTIONS = ("Y", "N")


class JudgeError(RuntimeError):
    """The judge could not be asked or gave no usable distribution."""


@dataclass
class Answer:
    probs: Dict[str, float]
    # Share of the model's top-k probability mass that landed on an allowed option.
    # Low coverage means the model wanted to say something else: treat the answer as a guess.
    coverage: float


@dataclass
class Verdict:
    p_local: float
    difficulty: Dict[str, float]
    expected_difficulty: float
    coverage: float
    raw: Dict[str, Answer] = field(default_factory=dict)


def distribution(top_logprobs: Sequence[dict], options: Sequence[str]) -> Answer:
    """Fold a top-k list into a distribution over *options*.

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


class Judge:
    def __init__(self, base_url: str, model: str, timeout_s: float = 30, opener=None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout_s = timeout_s
        self._open = opener or urllib.request.urlopen

    def ask(self, question: str, brief: str, options: Sequence[str]) -> Answer:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"{question}\n\n<brief>\n{brief.strip()}\n</brief>"},
            ],
            "max_tokens": 1,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": 20,
            # Reasoning models would spend the single token on "<think>"; llama.cpp passes this
            # through to the chat template. Harmless for templates that ignore it.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._open(req, timeout=self.timeout_s) as resp:
                payload = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise JudgeError(f"judge request failed: {exc}") from exc
        try:
            top = payload["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        except (KeyError, IndexError, TypeError) as exc:
            raise JudgeError("judge response carried no logprobs; is the server llama.cpp?") from exc
        return distribution(top, options)

    def judge(self, brief: str) -> Verdict:
        diff = self.ask(DIFFICULTY_Q, brief, DIFFICULTY_OPTIONS)
        local = self.ask(LOCAL_Q, brief, LOCAL_OPTIONS)
        expected = sum(int(k) * p for k, p in diff.probs.items())
        return Verdict(
            p_local=local.probs["Y"],
            difficulty=diff.probs,
            expected_difficulty=expected,
            coverage=min(diff.coverage, local.coverage),
            raw={"difficulty": diff, "local": local},
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


__all__: List[str] = ["Judge", "JudgeError", "Verdict", "Answer", "distribution", "verdict_dict"]
