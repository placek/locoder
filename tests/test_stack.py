import copy
import importlib.util
from pathlib import Path

import pytest
import yaml

from routing import settings
from routing.judge import WORKER

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stack", ROOT / "scripts" / "stack.py")
stack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stack)

PRESETS = """
[*]
flash-attn = on
load-mode = mmap

[big]
model = /models/big.gguf
ctx-size = 131072
cache-type-k = q8_0
cache-type-v = q8_0
parallel = 1
load-on-startup = true

[judge]
model = /models/judge.gguf
n-gpu-layers = 0
cache-type-k = f16
cache-type-v = f16
"""
CONFIG = {
    "model": {"default": "big", "provider": "llama-router", "context_length": 131072},
    "custom_providers": [{"name": "llama-router", "model": "big", "models": {"big": {}}},
                         {"name": "openrouter", "model": "x/y", "models": {"x/y": {}}}],
    "delegation": {"model": "big", "max_concurrent_children": 1, "max_iterations": 60},
}
ROUTING = {"llama": {"judge_model": "judge"}}
GOOD_WORKER = "a model (128k context; at most 60 steps)"


def check(presets=PRESETS, config=CONFIG, routing=ROUTING, worker=GOOD_WORKER):
    return stack.problems(stack.parse(presets), config, routing, worker)


def edited(**paths):
    """CONFIG with dotted paths replaced, e.g. edited(**{"model.context_length": 65536})."""
    cfg = copy.deepcopy(CONFIG)
    for path, value in paths.items():
        *parents, leaf = path.split(".")
        node = cfg
        for p in parents:
            node = node[p]
        node[leaf] = value
    return cfg


def test_the_repo_itself_is_consistent():
    presets = stack.parse((ROOT / "llama" / "presets.ini").read_text())
    config = yaml.safe_load((ROOT / "profile" / "config.yaml").read_text())
    routing = settings.load(ROOT / "profile" / "routing.yaml")
    assert stack.problems(presets, config, routing, WORKER) == []


def test_a_consistent_stack_has_no_problems():
    assert check() == []


def test_context_length_must_match_the_orchestrator_ctx_size():
    [msg] = check(config=edited(**{"model.context_length": 65536}))
    assert "must match" in msg and "131072" in msg


def test_context_below_hermes_minimum_is_refused():
    small = PRESETS.replace("ctx-size = 131072", "ctx-size = 32768")
    assert any("below Hermes' minimum" in m for m in check(small, edited(**{"model.context_length": 32768}),
                                                           worker="(32k context; at most 60 steps)"))


def test_delegation_must_name_a_preset_with_as_many_slots_as_children():
    assert any("delegation.model is 'coder'" in m for m in check(config=edited(**{"delegation.model": "coder"})))
    [msg] = check(config=edited(**{"delegation.max_concurrent_children": 3}))
    assert "parallel = 1" in msg
    no_parallel = PRESETS.replace("parallel = 1\n", "")
    assert any("sets no parallel" in m for m in check(no_parallel))


def test_the_local_provider_may_only_list_presets():
    cfg = edited(**{"custom_providers": [{"name": "llama-router", "model": "big",
                                          "models": {"big": {}, "coder": {}}}]})
    [msg] = check(config=cfg)
    assert "'coder'" in msg


def test_the_judge_must_be_a_separate_cpu_preset():
    assert any("evict" in m for m in check(routing={"llama": {"judge_model": "big"}}))
    on_gpu = PRESETS.replace("n-gpu-layers = 0\n", "")
    assert any("n-gpu-layers = 0" in m for m in check(on_gpu))


def test_mixed_kv_cache_types_under_flash_attention_are_refused():
    mixed = PRESETS.replace("cache-type-v = q8_0", "cache-type-v = q4_0")
    [msg] = check(mixed)
    assert "q8_0" in msg and "q4_0" in msg
    assert check(mixed.replace("flash-attn = on", "flash-attn = off")) == []


@pytest.mark.parametrize("line", ["n-gpu-layers = 99", "cpu-moe = true", "n-cpu-moe = 36", "ngl = 99"])
def test_hand_placed_layers_would_switch_fit_off(line):
    in_defaults = PRESETS.replace("[*]\n", f"[*]\n{line}\n")
    [msg] = check(in_defaults)
    assert "--fit" in msg and "[*]" in msg
    in_preset = PRESETS.replace("[big]\n", f"[big]\n{line}\n")
    [msg] = check(in_preset)
    assert "[big]" in msg
    assert check(in_preset.replace("[big]\n", "[big]\nfit = off\n")) == []


def test_the_judge_describes_the_worker_it_is_asked_about():
    msgs = check(worker="a model (256k context; at most 60 steps)")
    assert any("128k context" in m for m in msgs)
    msgs = check(worker="a model (128k context; at most 30 steps)")
    assert any("at most 60 steps" in m for m in msgs)


def test_keys_the_router_did_not_echo_were_ignored():
    presets = stack.parse(PRESETS)
    file_keys = stack.effective(presets, "big")
    echoed = "[big]\nflash-attn = on\nload-mode = mmap\nmodel = /models/big.gguf\nctx-size = 131072\n" \
             "cache-type-k = q8_0\ncache-type-v = q8_0\nparallel = 1\n"
    assert stack.ignored_keys(file_keys, echoed) == []
    assert stack.ignored_keys({**file_keys, "cache-typ-k": "q8_0"}, echoed) == ["cache-typ-k"]


def test_the_judge_switch_names_two_different_built_in_judges():
    def judged(**choice):
        return check(routing={"llama": {"judge_model": "judge"}, "judge": choice})

    assert judged(backend="julia", shadow="semif") == []
    assert judged(backend="semif", shadow="none") == []
    assert any("judge.backend is 'jev'" in m for m in judged(backend="jev", shadow="none"))
    assert any("judge.shadow is 'maybe'" in m for m in judged(backend="semif", shadow="maybe"))
    assert any("only agree with themselves" in m for m in judged(backend="julia", shadow="julia"))


def test_julia_alone_needs_no_judge_preset():
    no_judge = PRESETS.split("[judge]")[0]
    assert check(no_judge, routing={"judge": {"backend": "julia", "shadow": "none"}}) == []
    assert any("not a preset" in m for m in check(no_judge, routing={"judge": {"backend": "julia", "shadow": "semif"}}))


def test_the_retired_shadow_judge_block_is_reported():
    msgs = check(routing={"llama": {"judge_model": "judge"}, "shadow_judge": {"enabled": True}})
    assert any("no longer read" in m for m in msgs)
