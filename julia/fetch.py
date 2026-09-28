"""Build step: download Julia-1 into /model and prove it is the checkpoint we mean to run.

The model repository holds the weights and the upstream runtime (the `julia` package) together.
The revision comes from JULIA_REVISION; the weights must hash to JULIA_WEIGHTS_SHA256, so an
upstream re-upload fails the build instead of silently changing the judge. What was fetched
is written to /model/trismegistos-build.json, which /health reports.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

repo = os.environ["JULIA_REPO"]
revision = os.environ["JULIA_REVISION"]
want = os.environ["JULIA_WEIGHTS_SHA256"].lower()
root = Path(os.environ.get("JULIA_CHECKPOINT", "/model"))

commit = HfApi().model_info(repo, revision=revision).sha
snapshot_download(repo, revision=commit, local_dir=str(root))

digest = hashlib.sha256()
with (root / "model.safetensors").open("rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        digest.update(chunk)
got = digest.hexdigest()
if got != want:
    sys.exit(f"{repo}@{commit}: model.safetensors is {got}, JULIA_WEIGHTS_SHA256 expects {want}.\n"
             "Upstream changed the weights: check what changed, then update the hash in the Makefile.")

# The runtime's own dependencies, if the repository declares them.
requirements = root / "requirements.txt"
if requirements.is_file():
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", str(requirements)], check=True)

(root / "trismegistos-build.json").write_text(json.dumps(
    {"repo": repo, "revision": revision, "commit": commit, "weights_sha256": got}, indent=1))
print(f"{repo}@{commit}: weights verified")
