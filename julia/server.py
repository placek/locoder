"""Julia-1 over HTTP, one of the routing plugin's judges (routing.yaml: judge, julia).

    POST /predict  {"state": ..., "questions": {...}}  ->  {"answers": {...}}
    GET  /health   ->  what was built: repository, commit, weights hash, settings

/predict is Julia-1's typed API unchanged: the upstream PyTorch runtime that ships in the model
repository (julia.inference, julia.typed), on the CPU, with the settings Supersonic Labs
evaluated: 1,024 tokens, a 512-token question head, and strict encoding, so an input that does
not fit is refused (400) instead of silently truncated. Nothing but the standard library is
imported until load(), so the tests can drive the handler with a fake model.

    python server.py              serve on $JULIA_PORT (8080)
    python server.py --self-test  load the model, answer one question of each type, exit
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict

CHECKPOINT = Path(os.environ.get("JULIA_CHECKPOINT", "/model"))
SETTINGS = {"max_length": 1024, "head_length": 512, "strict_encoding": True}
MAX_BODY = 1 << 20
Predict = Callable[[Any, Dict[str, Any]], Dict[str, Any]]


def make_handler(predict: Predict, info: Dict[str, Any]):
    lock = threading.Lock()  # one forward pass at a time: the engine is not shared-state safe

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # one line per request on stderr, for journalctl
            sys.stderr.write("%s %s\n" % (self.command, fmt % args))

        def _send(self, code: int, body: Dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, info)
            else:
                self._send(404, {"error": "GET /health or POST /predict"})

        def do_POST(self):
            if self.path != "/predict":
                self._send(404, {"error": "GET /health or POST /predict"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                self._send(413 if length else 400, {"error": f"body must be 1..{MAX_BODY} bytes"})
                return
            try:
                req = json.loads(self.rfile.read(length))
            except ValueError:
                self._send(400, {"error": "body is not JSON"})
                return
            if not isinstance(req, dict) or "state" not in req or not isinstance(req.get("questions"), dict):
                self._send(400, {"error": "expected {\"state\": ..., \"questions\": {...}}"})
                return
            try:
                with lock:
                    out = predict(req["state"], req["questions"])
            except ValueError as exc:  # Julia's own refusals: overlong input, bad options or criteria
                self._send(400, {"error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001 - reported to the caller, which records it
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self._send(200, out)

    return Handler


def load() -> tuple:
    """The upstream runtime from the checkpoint directory, and what the image was built from."""
    sys.path.insert(0, str(CHECKPOINT))
    import torch

    torch.set_num_threads(int(os.environ.get("JULIA_THREADS", "2")))
    from julia.inference import load_model
    from julia.typed import predict_typed

    engine = load_model(str(CHECKPOINT), device="cpu", **SETTINGS)
    build = CHECKPOINT / "locoder-build.json"
    info = json.loads(build.read_text()) if build.is_file() else {}
    info.update(SETTINGS, threads=torch.get_num_threads())
    return (lambda state, questions: predict_typed(engine, state, questions)), info


SELF_TEST = {
    "state": "Goal: fix the typo 'recieve' in README.md.\nAcceptance: grep -q receive README.md",
    "questions": {
        "local": {"type": "noul", "instructions": "Will a small coding model finish this correctly?",
                  "criteria": {"false": "it fails", "true": "it finishes and the check passes"}},
        "difficulty": {"type": "score", "instructions": "How hard is this task?",
                       "criteria": ["trivial", "routine", "demanding", "heavy"]},
        "kind": {"type": "choice", "instructions": "What kind of change is this?",
                 "criteria": {"docs": "documentation", "code": "program logic"}},
    },
}


def main(argv) -> int:
    predict, info = load()
    if "--self-test" in argv:
        answers = predict(SELF_TEST["state"], SELF_TEST["questions"])["answers"]
        for name, question in SELF_TEST["questions"].items():
            probs = answers[name]["probabilities"]
            if abs(sum(probs.values()) - 1) > 0.01:
                raise SystemExit(f"self-test: {name} probabilities do not sum to 1: {probs}")
        print(json.dumps({"info": info, "answers": answers}, indent=1))
        return 0
    port = int(os.environ.get("JULIA_PORT", "8080"))
    sys.stderr.write(f"julia-1 on :{port}: {json.dumps(info)}\n")
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(predict, info)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
