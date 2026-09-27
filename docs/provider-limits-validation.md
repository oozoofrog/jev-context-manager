# Provider constraints without local usage quotas — dev.8

The 2026-09-28 user instruction removes arbitrary local limits because Jev is
intended for frequent, high-volume use. This supersedes the earlier call-budget
policy in the original design and previous release reports.

## Behavior

- No daily call quota, monetary/token spending budget, request-byte admission
  limit, response-byte quota, or candidate-count cutoff. Existing profiles may
  still contain old fields, but the runtime ignores them. `enable` no longer
  exposes `--max-daily-calls`.
- All eligible historical candidates reach Jev. Input packing uses a soft estimate
  against the published 32k state-plus-longest-question and 64k total context
  dimensions. UTF-8 bytes / 3 is not an exact tokenizer and never refuses a request
  on its own. [Provider model contract](https://docs.typesafe.ai/models).
- Confirmed server context errors recursively split retrieval batches and source
  spans, retaining contiguous offsets and original text. Generic HTTP 400 errors
  are not treated as context errors. An indivisible request or relationship pair
  remains explicitly unresolved with local source preserved.
- Successes and confirmed context rejection paths are cached, with a database
  index for frequent lookups. A repeated recovery reuses both successful child
  judgments and the known split tree.
- HTTP 429/5xx retries respect `Retry-After` seconds/HTTP-date and `retry-after-ms`.
  Long waits recheck policy and renew worker leases. The existing three-attempt
  per-invocation transient-failure policy remains; this does not restrict workload
  volume, and failed work can be retried on a later invocation.
- Private diagnostics record status, request ID, redacted body and selected retry
  headers. `forget` removes these derivatives. Journal v1 upgrades to v2 without
  removing records. Older runtimes reject v2 so their older deletion logic cannot
  overlook the new diagnostics; executable-only rollback cannot downgrade a store.
- Trusted direct bootstrap invocations with supported options are recognized as
  internal output, including omission of an equivalent default home. Arbitrary
  shell wrappers or files containing copied recovery output are not silently
  treated as trusted internal commands. Existing duplicate records are retained.

The 48KB output pagination target remains lossless delivery sizing, not a history
or Jev usage cap. Project/plugin disablement, deletion, source scope, policy epoch
checks and independent evidence boundaries remain in force.

## Evidence

- **Regression suite:** 104 tests passed, covering legacy quota bypass, requests
  over 80KB, more than 64 candidates, both packing dimensions, actual-shape context
  errors, recursive retrieval/worker recovery, cached split reuse, generic 400,
  interruptible Retry-After, diagnostic deletion and schema migration.
  [Full test log](../evidence/provider-limits-tests.log).
- **Real Jev, synthetic inputs:** a 106,341-byte request succeeded, reporting
  18,281 input tokens. A dense synthetic identifier record caused three confirmed
  `max_tokens_exceeded` responses, was split into five successful source spans,
  and reconstructed exactly. Six calls succeeded in total. Legacy profile values
  of one call/day and one request byte did not block execution.
  [Live evidence](../evidence/provider-limits-live.json).
- **Failure discovery:** the first development probe exposed Jev's actual error
  shape, `{"detail":{"error_type":"max_tokens_exceeded"}}`; the initial parser
  did not recognize it. A regression test was added, the parser corrected, and a
  fresh final fixture passed. Initial development logs remain in `.task-notes`.
- **Isolated installation:** all 13 installation/lifecycle checks passed, including
  bundle integrity, follower stop on disable, retained records after uninstall and
  reinstall, and unchanged user configuration. This used a temporary Codex home.
  [Lifecycle evidence](../evidence/provider-limits-plugin-validation.json).
- **Distribution:** plugin/skill validation and source-to-bundle checks passed;
  the dev.8 wheel was built and its Python files compared with the source. The
  first no-build-isolation wheel attempt lacked setuptools; the standard isolated
  build succeeded without changing the user's global Python environment.

No new independent Codex-session recovery or production Desktop hook approval is
claimed by this change. No real project histories were submitted in the new API
fixtures. These source/package checks did not upgrade the user installation. Subsequent
publication and installation use the [release pipeline](releasing.md), whose
result.json separately records the exact commit, CI, backup and installed checks.
