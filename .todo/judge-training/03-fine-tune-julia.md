# 03: Fine-tune Julia-1 on this machine's labels

Status: ready-for-agent
Blocked by: 01, 02

## What to build

`make train-julia` fine-tunes Julia-1 on the `local` question on the CPU, in a container
built from the Julia-1 image. Each training row is the brief, the question with its
described outcomes, and the label. It fits on older labels and early-stops on the newest
fifth.

It prints held-out Brier, log loss and near-certain picks for three models:
- the current model, calibrated;
- the new checkpoint;
- the new checkpoint, calibrated.

It promotes the new checkpoint only if it wins by a margin. A promoted checkpoint:
- goes into the state directory with a manifest (base commit, training window, label counts
  from real attempts and from replays, metrics, weights hash);
- is served by the same service, from an image built from that local checkpoint and
  hash-checked against its manifest;
- invalidates Julia-1's calibration, which is refitted.

`make julia BASE=1` rebuilds from the published weights, which is the rollback.

Use the upstream training entry point if the model repository ships one. Otherwise, use a
plain loop: cross-entropy on the decision logits against the target option, with inputs
encoded by the upstream serializer. Below 200 labels it changes nothing and says how many
more are needed.

## Acceptance criteria

- [ ] Fewer than 200 labels: nothing is trained, and the output names the shortfall.
- [ ] Training rows use the same question, criteria and encoding the service asks with; briefs Julia-1 cannot encode losslessly are left out and counted.
- [ ] A checkpoint that does not beat the current model on the held-out split is not promoted, and the service is unchanged.
- [ ] A promoted checkpoint is served, `make status` and `/health` name it, and its manifest records base, window, counts, metrics and hash.
- [ ] An image built from a local checkpoint whose hash differs from its manifest fails the build.
- [ ] `make julia BASE=1` serves the published weights again.
- [ ] Promotion clears Julia-1's calibration and refits it.
- [ ] Nothing in the process sends a brief or a label off the machine.
