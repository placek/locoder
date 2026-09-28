# 01: Graft installed, pinned, in both places

Status: done; the sandbox image is still to verify on alpha
Blocked by: none

## What to build

The pinned graft version is installed on the host (`make graft`, into the prefix, on the
`trismegistos` wrapper's PATH) and baked into the sandbox image, from one `GRAFT_VERSION`.
Telemetry is off in both: `DO_NOT_TRACK=1` in the image and the wrapper. `make check`
reports both versions and fails when either is missing or differs from the pin.

## Acceptance criteria

- [x] `make install` installs graft on the host and in the sandbox image at `GRAFT_VERSION`.
- [x] The `trismegistos` wrapper puts the host graft on PATH and sets `DO_NOT_TRACK=1`.
- [ ] `make check` fails on a missing or mismatched graft, host or sandbox.

Verified in a cloud container: `make graft` installs 0.20.0 and is idempotent; `make check`
reports `graft (host) 0.20.0`. No Docker daemon there, so the sandbox layer (the same pinned
`npm install -g`) and its check are for alpha: `make install && make check`.
