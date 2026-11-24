# ACC public synthetic workflow v0.8.2 — explicit intent correlation

Date 2026-11-24. **Public synthetic-only, not a private executor. Not merged or run.**

## Problem solved

The first public-only GitHub workflow v0.7.5 returned a successful receipt with task and run IDs, but it had no unique request identifier in the `workflow_dispatch` inputs. After losing an HTTP response for a future automated dispatch, the ACC controller could not safely determine which workflow run came from which dispatch request, and could duplicate an artificial task. Its old workflow and public first-run evidence remain unchanged.

## New separate workflow

Adds `.github/workflows/acc-public-synthetic-v082.yml` without modifying v0.7.5's pinned source. Only `workflow_dispatch` is enabled. It requires exactly three inputs:

- `intent_id` — an opaque synthetic-only `lab-intent-` prefix plus 16 lowercase hexadecimal digits, e.g. `lab-intent-0123456789abcdef`.
- `task_id` — fixed `lab-artifact-001` (private RC83 rejected).
- `source_sha256` — fixed SHA256 `1ef077c75ce964432b4031dc96ce20ffcf523c51447254e7a1d4ac3a6274da21` of artificial source.

The run title is `ACC-SYNTH-${{ inputs.intent_id }}`, and the GitHub concurrency group includes the same ID. The job creates only the exact fixed artificial candidate, checks SHA256, then emits a canonical JSON receipt including `intent_id`, `run_id`, `run_commit`, source/candidate hashes and false flags for model usage, private code usage, private RC83 execution and namespace proof. `SYNTHETIC_NEEDS_REVIEW` remains the only successful result classification. Standard public GitHub-hosted `ubuntu-latest`, fixed 5-minute job timeout, `permissions: contents: read`, no external actions or arbitrary user commands. No automatically triggered event (`push`, `pull_request` or scheduled runs).

**Important limitation:** GitHub concurrency is **not a durable exactly-once ledger**; a second dispatch after the first run finishes can still create a duplicate. The future ACC Worker must atomically persist dispatch intent *before* one POST and must reconcile any uncertain response against provider run metadata by `intent_id` before retry. No automatic retry after a possibly accepted POST. No such dispatcher is deployed or authorized by this PR.

## QA actually executed

Exactly **25/25** local Python tests PASS by extracting and running the production Python code embedded in the workflow. Tests include valid canonical receipt and SHA, malformed/missing/uppercase/short/injection-shaped IDs, wrong task, wrong repo, wrong source hash, invalid run ID/commit, missing sandbox directory, source byte preservation, the `run-name`/concurrency binding, no `uses:` external actions and no automatic triggers. No actual GitHub Actions execution is claimed.

This is PUBLIC only. It cannot take private RC83 code, customer documents, external model API tokens or billable runners. User's GitHub private Actions budgets remain zero. The user must review and merge a separate **public** PR before the new workflow is available in `main`; no private RC83/ACC PR is auto-merged.
