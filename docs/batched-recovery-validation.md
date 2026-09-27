# Batched recovery validation

Release candidate: `0.1.0-dev.6` (Python `0.1.0.dev6`). Validated on
2026-09-28 KST. This remains a prerelease.

Jev requests now use bounded source spans sized against the encoded request,
including questions. Retrieval judgments are combined across batches; adjacent
user corrections are compared separately across batch boundaries. Failed or
unassessed source spans remain available locally with explicit coverage gaps.
Existing egress policy, call budgets, caches and epoch guards still apply.

Oversized recovery packs are delivered through immutable, lossless pages.
Receipts distinguish partially served pages from all pages served; neither
state asserts that the agent understood or acted on the content. The skill
follows each returned `next_read_command` before continuing the task.

## Evidence

- The 88-test regression suite covers byte ceilings, cached decisions, denied
  egress, budgets, partial failures, cross-batch corrections, classifier span
  provenance, page corruption, repeated/out-of-order reads, epoch changes and
  forgetting during pagination. It also exercises the bundled launcher.
  See [test log](../evidence/batched-recovery-tests.log).
- An isolated replay of the actual blocked pack used 783,693 input bytes and
  64 records. It produced 21 pages, with a largest response of 47,710 bytes
  against a 48,000-byte limit. Reconstruction preserved the selected text and
  snapshot exactly. No Jev calls or actual project changes were made.
  See [page results](../evidence/batched-pack-validation.json).
- Real Jev validation used only synthetic fixtures with explicit egress and
  a six-call cap. The final run made three successful HTTP calls of 4,169,
  4,399 and 4,464 bytes against a 7,000-byte ceiling. The pack had normal
  quality and identified the correction across batches. An earlier run also
  made three successful calls; six calls were made in total.
  See [final Jev results](../evidence/batched-jev-validation.json).

These results do not establish fresh independent-agent continuation or Desktop
hook-approval behavior for this revision. The actual project's egress policy
was unchanged. Oversized comparison pairs, requests that cannot fit intact,
unavailable credentials and exhausted budgets remain explicit gaps or degraded
recovery; batching does not bypass those constraints.
