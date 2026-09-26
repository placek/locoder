# The container every terminal() call of the agent runs in (Hermes' docker
# terminal backend). Hermes itself runs on the host; only the commands the
# agent issues land here. Built by `make install` as locoder-sandbox:local.
#
# Keep it a toolbox, not a platform: language runtimes and the CLI tools the
# skills lean on. Per-project dependencies belong in the project (a venv,
# node_modules), which lives on the mounted /srv/data/projects anyway.
FROM docker.io/nikolaik/python-nodejs:python3.13-nodejs22

# gh: /setup and the tracker flows in the skills; ripgrep/fd/jq: what the
# agent reaches for when searching; build-essential: native wheels and addons.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      gh ripgrep fd-find jq less make build-essential sqlite3 \
 && ln -s /usr/bin/fdfind /usr/local/bin/fd \
 && rm -rf /var/lib/apt/lists/*

# uv for Python projects; the base image ships pip only.
RUN pip install --no-cache-dir uv

# docker_run_as_host_user runs this as your uid, which has no passwd entry
# here; give such users a writable HOME so tools can cache.
ENV HOME=/tmp/home
RUN mkdir -p /tmp/home && chmod 1777 /tmp/home

WORKDIR /srv/data/projects
