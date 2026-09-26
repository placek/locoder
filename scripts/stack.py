"""The settings that must agree across llama/presets.ini, profile/config.yaml and routing.yaml.

None of these mismatches fails where it is made. Hermes rejects a model whose context it
believes too small; a delegate_task child queues for a slot that does not exist; a hand-set
layer count makes llama.cpp's --fit step aside, and the model no longer fits in VRAM; mixed
K/V cache types push flash attention onto the CPU. So they are checked before anything runs:
`make test` on the repo's own files (tests/test_stack.py), `make check` on the installed ones.

Plain Python on purpose: the tests import it without Hermes, check.py inside Hermes' venv.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# hermes-agent's MINIMUM_CONTEXT_LENGTH: a smaller model context is refused.
HERMES_MIN_CONTEXT = 64000
# Any of these, in a preset or in [*], makes --fit step aside and use them as given. Every
# spelling the router accepts: long and short option names and their environment variables.
PLACEMENT = {
    "n-gpu-layers", "gpu-layers", "ngl", "LLAMA_ARG_N_GPU_LAYERS",
    "cpu-moe", "cmoe", "LLAMA_ARG_CPU_MOE",
    "n-cpu-moe", "ncmoe", "LLAMA_ARG_N_CPU_MOE",
    "override-tensor", "ot",
    "tensor-split", "ts", "LLAMA_ARG_TENSOR_SPLIT",
}
# Preset-only keys: the router consumes them and does not echo them back as options.
PRESET_ONLY = {"load-on-startup"}
DEFAULTS_SECTION = "*"

Presets = Dict[str, Dict[str, str]]


def parse(ini: str) -> Presets:
    """Sections of a llama.cpp presets file, as the router reads it: `;` and `#` start comments."""
    out: Presets = {}
    current = DEFAULTS_SECTION
    for raw in ini.splitlines():
        line = raw.split(";", 1)[0].split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            out.setdefault(current, {})
        elif "=" in line:
            key, value = (s.strip() for s in line.split("=", 1))
            out.setdefault(current, {})[key] = value
    return out


def models(presets: Presets) -> List[str]:
    return [name for name in presets if name != DEFAULTS_SECTION]


def effective(presets: Presets, name: str) -> Dict[str, str]:
    """A preset's options with [*] underneath, as the router applies them."""
    return {**presets.get(DEFAULTS_SECTION, {}), **presets.get(name, {})}


def _int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _off(value: Optional[str]) -> bool:
    return str(value).strip().lower() in ("off", "false", "0", "no", "disabled")


def problems(presets: Presets, config: dict, routing: dict, worker: Optional[str] = None) -> List[str]:
    """Every disagreement found, as sentences that say what to change. Empty when consistent."""
    found: List[str] = []
    names = set(models(presets))
    model = config.get("model") or {}
    delegation = config.get("delegation") or {}

    orchestrator = model.get("default")
    if orchestrator not in names:
        found.append(f"model.default is {orchestrator!r}, which is not a preset in llama/presets.ini")
    else:
        want = _int(effective(presets, orchestrator).get("ctx-size"))
        have = _int(model.get("context_length"))
        if want is None:
            found.append(f"the {orchestrator} preset sets no ctx-size; model.context_length has nothing to match")
        elif have != want:
            found.append(f"model.context_length is {model.get('context_length')} but the {orchestrator} "
                         f"preset's ctx-size is {want}: they must match")
        if have is not None and have < HERMES_MIN_CONTEXT:
            found.append(f"model.context_length {have} is below Hermes' minimum of {HERMES_MIN_CONTEXT}")

    child = delegation.get("model")
    if child not in names:
        found.append(f"delegation.model is {child!r}, which is not a preset in llama/presets.ini")
    else:
        slots = _int(effective(presets, child).get("parallel"))
        wanted = _int(delegation.get("max_concurrent_children"))
        if slots is None:
            found.append(f"the {child} preset sets no parallel; llama.cpp's automatic slot count would split "
                         "its context")
        elif wanted != slots:
            found.append(f"delegation.max_concurrent_children is {delegation.get('max_concurrent_children')} "
                         f"but the {child} preset has parallel = {slots}: they must match")

    for provider in config.get("custom_providers") or []:
        if provider.get("name") != model.get("provider"):
            continue
        listed = set((provider.get("models") or {}).keys()) | {provider.get("model")}
        for name in sorted(n for n in listed if n and n not in names):
            found.append(f"the {provider['name']} provider lists model {name!r}, which is not a preset")

    judge = ((routing.get("llama") or {}).get("judge_model")) or "judge"
    if judge not in names:
        found.append(f"llama.judge_model is {judge!r}, which is not a preset in llama/presets.ini")
    elif judge in (orchestrator, child):
        found.append(f"llama.judge_model is {judge!r}, a single-slot model the agent runs on: every judge "
                     "query would evict its KV cache")
    elif _int(effective(presets, judge).get("n-gpu-layers")) != 0:
        found.append(f"the {judge} preset must set n-gpu-layers = 0: the judge runs on the CPU and leaves "
                     "the VRAM to the orchestrator")

    for name in sorted(names):
        opts = effective(presets, name)
        if not _off(opts.get("flash-attn", "on")):
            k, v = opts.get("cache-type-k", "f16"), opts.get("cache-type-v", "f16")
            if k != v:
                found.append(f"the {name} preset mixes cache-type-k = {k} with cache-type-v = {v}: the official "
                             "CUDA image has flash-attention kernels only for matching types")

    for name in sorted({orchestrator, child} & names):
        opts = effective(presets, name)
        if _off(opts.get("fit", "on")):
            continue
        for section in (DEFAULTS_SECTION, name):
            hand_set = sorted(PLACEMENT & presets.get(section, {}).keys())
            if hand_set:
                found.append(f"{', '.join(hand_set)} in [{section}] makes --fit step aside for {name}: drop it, "
                             "or set fit = off and place the layers by hand")

    if worker is not None and child in names:
        ctx = _int(effective(presets, child).get("ctx-size"))
        if ctx and f"{ctx // 1024}k context" not in worker:
            found.append(f"the judge's WORKER text does not say '{ctx // 1024}k context' ({child}'s ctx-size)")
        steps = delegation.get("max_iterations")
        if steps is not None and f"at most {steps} steps" not in worker:
            found.append(f"the judge's WORKER text does not say 'at most {steps} steps' (delegation.max_iterations)")
    return found


def ignored_keys(file_keys: Dict[str, str], echoed_ini: str) -> List[str]:
    """Keys the file sets that the router's echo of the preset lacks: it ignored them (a typo, an
    option this llama.cpp build does not have), or they were written under an alias."""
    echoed = set()
    for section in parse(echoed_ini).values():
        echoed |= section.keys()
    return sorted(k for k in file_keys if k not in echoed and k not in PRESET_ONLY)
