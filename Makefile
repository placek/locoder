# locoder — local coding agent stack: llama.cpp router + pinned Hermes + profile.
#
# `make install` builds everything into place, like any other build: each
# target is a real file (a symlink, a unit, a venv stamp, an image stamp) and
# is only redone when its source changed. Nothing here needs root; NixOS only
# provides the Nvidia driver, Docker and nvidia-container-toolkit.
#
#   make install        build/link everything (idempotent)
#   make enable         start llama.cpp now and at login
#   make check          prove the installed stack is wired (plugins, tools, router, judge)
#   make tui            open the agent
#   make bump [REV=…]   move Hermes to a new revision, keep it only if `check` passes
#   make report         what the ledger says about the routing goals, the rungs and the judge

SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

REPO        := $(abspath .)
PREFIX      ?= $(HOME)/.local/share/locoder
STATE       ?= $(HOME)/.local/state/locoder
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
SANDBOX_IMAGE := locoder-sandbox:local

# ddgs: keyless search backend (web/ddgs in config.yaml).
HERMES_EXTRAS    ?= ddgs
DEFUDDLE_VERSION ?= 0.19.4
REV              ?= main

HERMES_REV    := $(shell cat hermes.rev)
HERMES_STAMP  := $(PREFIX)/hermes/.locoder-rev
HERMES_PY     := $(PREFIX)/hermes/.venv/bin/python
DEFUDDLE_BIN  := $(PREFIX)/tools/defuddle/node_modules/.bin/defuddle
LLAMA_IMAGE    = $(shell cat llama/image.lock)

PROFILE_FILES := config.yaml SOUL.md routing.yaml
PROFILE_LINKS := $(addprefix $(HERMES_HOME)/,$(PROFILE_FILES))
DIR_LINKS     := $(HERMES_HOME)/skills $(HERMES_HOME)/plugins
LLAMA_UNIT    := $(UNIT_DIR)/locoder-llama.service
WRAPPER       := $(BIN_DIR)/locoder

CHECK_ENV = HERMES_HOME=$(HERMES_HOME) HERMES_DEFUDDLE_BIN=$(DEFUDDLE_BIN) \
            LOCODER_PRESETS=$(REPO)/llama/presets.ini LOCODER_SANDBOX_IMAGE=$(SANDBOX_IMAGE)

.PHONY: help install hermes profile llama sandbox defuddle wrapper enable disable restart \
        status logs check check-offline test bump tui report bakeoff pin-llama uninstall

help:
	@sed -n 's/^#   make /  make /p' Makefile

install: hermes profile defuddle sandbox llama wrapper
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

# -- Terminal sandbox image ------------------------------------------------------
sandbox: $(STATE)/sandbox.stamp

$(STATE)/sandbox.stamp: sandbox/Dockerfile
	$(DOCKER) build --quiet --tag $(SANDBOX_IMAGE) sandbox
	@mkdir -p $(STATE) && touch $@

# -- llama.cpp router ---------------------------------------------------------------
llama: $(LLAMA_UNIT) $(STATE)/presets.stamp | $(STATE)/llama-cache

llama/image.lock:
	$(MAKE) --no-print-directory pin-llama

# Resolve the moving tag to a digest once; the unit runs the digest. Re-run to upgrade.
pin-llama:
	$(DOCKER) pull --quiet $(LLAMA_TAG) >/dev/null
	$(DOCKER) image inspect --format '{{index .RepoDigests 0}}' $(LLAMA_TAG) > llama/image.lock
	@echo "llama/image.lock -> $$(cat llama/image.lock); commit it"

$(LLAMA_UNIT): systemd/locoder-llama.service.in llama/image.lock Makefile | $(UNIT_DIR)
	sed -e 's|@REPO@|$(REPO)|g' -e 's|@STATE@|$(STATE)|g' -e 's|@MODELS@|$(MODELS)|g' \
	    -e 's|@PORT@|$(PORT)|g' -e 's|@DOCKER@|$(DOCKER)|g' -e 's|@GPU_ARGS@|$(GPU_ARGS)|g' \
	    -e 's|@UID@|$(shell id -u)|g' -e 's|@GID@|$(shell id -g)|g' -e 's|@IMAGE@|$(LLAMA_IMAGE)|g' \
	    $< > $@
	systemctl --user daemon-reload

# Presets are read at startup: restart a running router when they change.
$(STATE)/presets.stamp: llama/presets.ini
	@if systemctl --user is-active --quiet locoder-llama; then \
	    echo "presets changed: restarting locoder-llama"; systemctl --user restart locoder-llama; fi
	@mkdir -p $(STATE) && touch $@

# -- Entry point -----------------------------------------------------------------------
wrapper: $(WRAPPER)

$(WRAPPER): bin/locoder.in Makefile | $(BIN_DIR)
	sed -e 's|@HERMES_HOME@|$(HERMES_HOME)|g' -e 's|@PREFIX@|$(PREFIX)|g' $< > $@
	chmod +x $@

# -- Operating it ---------------------------------------------------------------------
enable:
	systemctl --user enable --now locoder-llama.service

disable:
	systemctl --user disable --now locoder-llama.service

restart:
	systemctl --user restart locoder-llama.service

status:
	@systemctl --user --no-pager status locoder-llama.service | head -5 || true
	@curl -fsS http://127.0.0.1:$(PORT)/v1/models | python3 -c \
	    'import json,sys; print("presets:", [m["id"] for m in json.load(sys.stdin)["data"]])' || true

logs:
	journalctl --user -u locoder-llama.service -f

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
	-systemctl --user disable --now locoder-llama.service
	rm -f $(LLAMA_UNIT) $(WRAPPER) $(PROFILE_LINKS) $(DIR_LINKS)
	systemctl --user daemon-reload
	rm -rf $(PREFIX)
	$(if $(PURGE),rm -rf $(STATE))
