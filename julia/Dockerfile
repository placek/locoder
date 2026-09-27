# Julia-1, Supersonic Labs' 144M-parameter decision model, as a CPU-only HTTP service: the
# routing plugin's shadow judge. Built by `make install` as locoder-julia:local and run by
# locoder-julia.service on 127.0.0.1 (routing.yaml: shadow_judge.base_url).
#
# The build downloads the model, checks the weights' hash, and answers one question of each
# type, so an image that builds is an image that answers.
FROM docker.io/library/python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 HF_HOME=/tmp/hf

# CPU-only torch (the CUDA wheels are gigabytes this never uses), then what the upstream
# runtime loads: the mmBERT tokenizer and encoder config, safetensors weights.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install huggingface_hub safetensors tokenizers transformers numpy

# The Makefile passes all three; see JULIA_REVISION and JULIA_WEIGHTS_SHA256 there.
ARG JULIA_REPO=SupersonicLabs/Julia-1
ARG JULIA_REVISION=main
ARG JULIA_WEIGHTS_SHA256
ENV JULIA_CHECKPOINT=/model
COPY fetch.py server.py /opt/julia/
RUN JULIA_REPO=$JULIA_REPO JULIA_REVISION=$JULIA_REVISION JULIA_WEIGHTS_SHA256=$JULIA_WEIGHTS_SHA256 \
    python /opt/julia/fetch.py \
 && python /opt/julia/server.py --self-test \
 && rm -rf /tmp/hf

# Two threads: fast enough for a judge (tens of ms a question) while the orchestrator's CPU
# experts keep the rest of the cores.
ENV JULIA_THREADS=2 JULIA_PORT=8080
# Everything is in /model now: never reach the network at run time, and give the unprivileged
# user a writable HOME for any cache a library insists on.
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HOME=/tmp
USER 65534:65534
EXPOSE 8080
CMD ["python", "/opt/julia/server.py"]
