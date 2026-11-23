# ACC synthetic Docker boundary experiment

Only a **public, synthetic-only** test. No private RC83 code, client files, access tokens, or paid APIs.

The workflow `.github/workflows/docker-boundary.yml` runs only on the branch `acc-synthetic-docker-boundary-20261123` when `.acc-docker-boundary-trigger` is created with the exact one-shot marker. It uses the standard public-repo `ubuntu-latest` runner, `contents: read`, and a five-minute maximum. **No model weights are downloaded.**

The test runs seven synthetic Python checks **inside** a pinned Docker image, confirming unprivileged UID, blocked outbound network, read-only root and worktree, zero effective Linux capabilities, no-new-privileges, and no host authentication variables, plus one harmless arithmetic smoke test. The Docker invocation retains `--network=none`, `--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, memory/CPU/PID bounds, and read-only source mount. If a boundary cannot be confirmed, the run must report `BLOCKED` and never silently weaken the isolation.

The result is a boundary smoke test, **not** a penetration test or proof of safety for all untrusted code. Cloudflare ACC v0.3.6, private RC83, and its downloaded Windows candidate remain unchanged.
