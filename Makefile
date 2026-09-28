# trismegistos — local coding agent stack: llama.cpp router + pinned Hermes + profile.
#
# `make install` builds everything into place, like any other build: each
# target is a real file (a symlink, a unit, a venv stamp, an image stamp) and
# is only redone when its source changed. Nothing here needs root; NixOS only
# provides the Nvidia driver, Docker and nvidia-container-toolkit.
#
#   make install        build/link everything (idempotent)
#   make enable         start the llama.cpp router and the Julia-1 judge, now and at login
#   make check          prove the installed stack is wired (plugins, tools, router, judge)
#   make tui            open the agent
#   make bump [REV=…]   move Hermes to a new revision, keep it only if `check` passes
#   make report         what the ledger says about the routing goals, the rungs and the judge
#   make bakeoff MODELS=a,b [TASKS=10]   replay recent tasks on candidate OpenRouter models
#   make julia          build the Julia-1 shadow judge image (part of install)
#   make test           unit tests and the presets/config consistency rules (no Hermes needed)

SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

REPO        := $(abspath .)
PREFIX      ?= $(HOME)/.local/share/trismegistos
STATE       ?= $(HOME)/.local/state/trismegistos
HERMES_HOME ?= $(STATE)/home
BIN_DIR     ?= $(HOME)/.local/bin
UNIT_DIR    ?= $(HOME)/.config/systemd/user

MODELS      ?= /srv/data/models
PORT        ?= 8088
# NixOS' nvidia-container-toolkit exposes GPUs through CDI. With the legacy
# runtime hook instead, use: make GPU_ARGS='--gpus all' install
GPU_ARGS    ?= --device=nvidia.com/gpu=all
DOCKER      ?= $(shell command -v docker)
LLAMA_TAG   ?= ghcr.io/ggml-org/llama.cpp:server-cuda
SANDBOX_IMAGE := trismegistos-sandbox:local
# Julia-1, one of the two judges (routing.yaml: judge, julia). The weights hash is SupersonicLabs/Julia-1's
# model.safetensors: the build fails if upstream changes it. Pin JULIA_REVISION to the commit
# `make status` reports once you trust it.
JULIA_IMAGE          := trismegistos-julia:local
JULIA_PORT           ?= 8089
JULIA_REPO           ?= SupersonicLabs/Julia-1
JULIA_REVISION       ?= main
JULIA_WEIGHTS_SHA256 ?= df853bf7fe424420011f3d0c47a05d7341aa9eefa7fb9f203ea4aada4ad95b72

# ddgs: keyless search backend (web/ddgs in config.yaml).
HERMES_EXTRAS    ?= ddgs
DEFUDDLE_VERSION ?= 0.19.4
# One pin for the host install and the sandbox image; bump both by changing it here.
GRAFT_VERSION    ?= 0.20.0
REV              ?= main

HERMES_REV    := $(shell cat hermes.rev)
HERMES_STAMP  := $(PREFIX)/hermes/.trismegistos-rev
HERMES_PY     := $(PREFIX)/hermes/.venv/bin/python
DEFUDDLE_BIN  := $(PREFIX)/tools/defuddle/node_modules/.bin/defuddle
GRAFT_DIR     := $(PREFIX)/tools/graft
GRAFT_STAMP   := $(GRAFT_DIR)/.trismegistos-$(GRAFT_VERSION)
LLAMA_IMAGE    = $(shell cat llama/image.lock)

PROFILE_FILES := config.yaml SOUL.md routing.yaml
PROFILE_LINKS := $(addprefix $(HERMES_HOME)/,$(PROFILE_FILES))
DIR_LINKS     := $(HERMES_HOME)/skills $(HERMES_HOME)/plugins
LLAMA_UNIT    := $(UNIT_DIR)/trismegistos-llama.service
JULIA_UNIT    := $(UNIT_DIR)/trismegistos-julia.service
UNITS         := trismegistos-llama.service trismegistos-julia.service
WRAPPER       := $(BIN_DIR)/trismegistos

CHECK_ENV = HERMES_HOME=$(HERMES_HOME) HERMES_DEFUDDLE_BIN=$(DEFUDDLE_BIN) \
            TRISMEGISTOS_PRESETS=$(REPO)/llama/presets.ini TRISMEGISTOS_SANDBOX_IMAGE=$(SANDBOX_IMAGE) \
            TRISMEGISTOS_GRAFT_VERSION=$(GRAFT_VERSION) PATH=$(GRAFT_DIR)/node_modules/.bin:$$PATH

.PHONY: help install hermes profile llama julia sandbox defuddle graft wrapper enable disable restart \
        status logs check check-offline test bump tui report bakeoff pin-llama uninstall

help:
	@sed -n 's/^#   make /  make /p' Makefile

install: hermes profile defuddle graft sandbox llama julia wrapper
	@echo "installed. next: make enable && make check"

# -- Hermes: pinned checkout + uv venv ------------------------------------------
hermes: $(HERMES_STAMP)

$(HERMES_STAMP): hermes.rev scripts/hermes-install.sh
	scripts/hermes-install.sh $(HERMES_REV) $(PREFIX) $(HERMES_EXTRAS) >/dev/null
	cp hermes.rev $@

# -- Profile: the Hermes home, every source file a symlink into this repo -------
profile: $(PROFILE_LINKS) $(DIR_LINKS) $(HERMES_HOME)/.no-bundled-skills $(HERMES_HOME)/.env

$(HERMES_HOME) $(STATE)/llama-cache $(BIN_DIR) $(UNIT_DIR):
	mkdir -p $@

$(HERMES_HOME)/%: profile/% | $(HERMES_HOME)
	ln -sfn $(REPO)/$< $@

# Skill discovery (rglob) will not descend into a symlink INSIDE a skills root,
# so the root itself is the link. Refuse to clobber a real directory.
$(DIR_LINKS): | $(HERMES_HOME)
	@if [ -e $@ ] && [ ! -L $@ ]; then echo "$@ is a real directory; move it away first" >&2; exit 1; fi
	ln -sfn $(REPO)/$(notdir $@) $@

# `hermes update`-style bundled-skill syncing must never write into the repo.
$(HERMES_HOME)/.no-bundled-skills: | $(HERMES_HOME)
	touch $@

# Secrets stay out of the repo: created once from the template, never overwritten.
$(HERMES_HOME)/.env: | $(HERMES_HOME)
	install -m 600 profile/env.example $@
	@echo "fill in $@ (OPENROUTER_API_KEY)"

# -- Tools the host-side plugins call -------------------------------------------
defuddle: $(DEFUDDLE_BIN)

$(DEFUDDLE_BIN):
	npm install --silent --no-audit --no-fund --prefix $(PREFIX)/tools/defuddle defuddle@$(DEFUDDLE_VERSION)
	test -x $@

# Graft on the host: route() wires projects with it, and Claude Code's MCP server runs it.
graft: $(GRAFT_STAMP)

$(GRAFT_STAMP):
	npm install --silent --no-audit --no-fund --prefix $(GRAFT_DIR) @nanonets/graft@$(GRAFT_VERSION)
	test -x $(GRAFT_DIR)/node_modules/.bin/graft
	rm -f $(GRAFT_DIR)/.trismegistos-* && touch $@

# -- Terminal sandbox image ------------------------------------------------------
sandbox: $(STATE)/sandbox-$(GRAFT_VERSION).stamp

$(STATE)/sandbox-$(GRAFT_VERSION).stamp: sandbox/Dockerfile
	$(DOCKER) build --quiet --build-arg GRAFT_VERSION=$(GRAFT_VERSION) --tag $(SANDBOX_IMAGE) sandbox
	@mkdir -p $(STATE) && rm -f $(STATE)/sandbox*.stamp && touch $@

# -- Julia-1 shadow judge (CPU) ------------------------------------------------------------
julia: $(JULIA_UNIT) $(STATE)/julia.stamp

# The build downloads the model, verifies the weights and answers a self-test.
$(STATE)/julia.stamp: julia/Dockerfile julia/fetch.py julia/server.py Makefile
	$(DOCKER) build --quiet --build-arg JULIA_REPO=$(JULIA_REPO) --build-arg JULIA_REVISION=$(JULIA_REVISION) \
	    --build-arg JULIA_WEIGHTS_SHA256=$(JULIA_WEIGHTS_SHA256) --tag $(JULIA_IMAGE) julia
	@if systemctl --user is-active --quiet trismegistos-julia; then \
	    echo "julia image rebuilt: restarting trismegistos-julia"; systemctl --user restart trismegistos-julia; fi
	@mkdir -p $(STATE) && touch $@

$(JULIA_UNIT): systemd/trismegistos-julia.service.in Makefile | $(UNIT_DIR)
	sed -e 's|@REPO@|$(REPO)|g' -e 's|@DOCKER@|$(DOCKER)|g' -e 's|@JULIA_PORT@|$(JULIA_PORT)|g' \
	    -e 's|@IMAGE@|$(JULIA_IMAGE)|g' $< > $@
	systemctl --user daemon-reload

# -- llama.cpp router ---------------------------------------------------------------
llama: $(LLAMA_UNIT) $(STATE)/presets.stamp | $(STATE)/llama-cache

llama/image.lock:
	$(MAKE) --no-print-directory pin-llama

# Resolve the moving tag to a digest once; the unit runs the digest. Re-run to upgrade.
pin-llama:
	$(DOCKER) pull --quiet $(LLAMA_TAG) >/dev/null
	$(DOCKER) image inspect --format '{{index .RepoDigests 0}}' $(LLAMA_TAG) > llama/image.lock
	@echo "llama/image.lock -> $$(cat llama/image.lock); commit it"

$(LLAMA_UNIT): systemd/trismegistos-llama.service.in llama/image.lock Makefile | $(UNIT_DIR)
	sed -e 's|@REPO@|$(REPO)|g' -e 's|@STATE@|$(STATE)|g' -e 's|@MODELS@|$(MODELS)|g' \
	    -e 's|@PORT@|$(PORT)|g' -e 's|@DOCKER@|$(DOCKER)|g' -e 's|@GPU_ARGS@|$(GPU_ARGS)|g' \
	    -e 's|@UID@|$(shell id -u)|g' -e 's|@GID@|$(shell id -g)|g' -e 's|@IMAGE@|$(LLAMA_IMAGE)|g' \
	    $< > $@
	systemctl --user daemon-reload

# Presets are read at startup: restart a running router when they change.
$(STATE)/presets.stamp: llama/presets.ini
	@if systemctl --user is-active --quiet trismegistos-llama; then \
	    echo "presets changed: restarting trismegistos-llama"; systemctl --user restart trismegistos-llama; fi
	@mkdir -p $(STATE) && touch $@

# -- Entry point -----------------------------------------------------------------------
wrapper: $(WRAPPER)

$(WRAPPER): bin/trismegistos.in Makefile | $(BIN_DIR)
	sed -e 's|@HERMES_HOME@|$(HERMES_HOME)|g' -e 's|@PREFIX@|$(PREFIX)|g' $< > $@
	chmod +x $@

# -- Operating it ---------------------------------------------------------------------
enable:
	systemctl --user enable --now $(UNITS)

disable:
	systemctl --user disable --now $(UNITS)

restart:
	systemctl --user restart $(UNITS)

status:
	@for u in $(UNITS); do systemctl --user --no-pager status $$u | head -3 || true; done
	@curl -fsS http://127.0.0.1:$(PORT)/v1/models | python3 -c \
	    'import json,sys; print("presets:", [m["id"] for m in json.load(sys.stdin)["data"]])' || true
	@curl -fsS http://127.0.0.1:$(JULIA_PORT)/health | python3 -c \
	    'import json,sys; h=json.load(sys.stdin); print("julia:", h.get("repo"), h.get("commit"))' || true

logs:
	journalctl --user -u trismegistos-llama.service -u trismegistos-julia.service -f

tui:
	@exec $(WRAPPER)

# -- Verification ---------------------------------------------------------------------
# The profile .env is sourced so the check sees what the running agent sees.
check:
	@set -a; [ ! -f $(HERMES_HOME)/.env ] || . $(HERMES_HOME)/.env; set +a; \
	    $(CHECK_ENV) $(HERMES_PY) scripts/check.py

check-offline:
	@$(CHECK_ENV) $(HERMES_PY) scripts/check.py --offline

test:
	uv run --no-project --quiet --with pytest --with pyyaml pytest -q tests

bump:
	scripts/hermes-bump.sh $(REV) $(PREFIX) $(REPO) $(HERMES_EXTRAS)
	cp hermes.rev $(HERMES_STAMP)

report:
	@HERMES_HOME=$(HERMES_HOME) $(HERMES_PY) scripts/report.py

# Replay recent delegated tasks on candidate OpenRouter models: make bakeoff MODELS=a,b [TASKS=10]
bakeoff:
	@test -n "$(MODELS)" || { echo "usage: make bakeoff MODELS=model-a,model-b [TASKS=10]"; exit 2; }
	@set -a; [ ! -f $(HERMES_HOME)/.env ] || . $(HERMES_HOME)/.env; set +a; \
	    HERMES_HOME=$(HERMES_HOME) $(HERMES_PY) scripts/bakeoff.py --models $(MODELS) --tasks $(or $(TASKS),10)

# Removes what `install` placed outside the repo. State (the Hermes home with
# sessions, memories and the routing ledger) survives unless PURGE=1.
uninstall:
	-systemctl --user disable --now $(UNITS)
	rm -f $(LLAMA_UNIT) $(JULIA_UNIT) $(WRAPPER) $(PROFILE_LINKS) $(DIR_LINKS)
	systemctl --user daemon-reload
	rm -rf $(PREFIX)
	$(if $(PURGE),rm -rf $(STATE))
