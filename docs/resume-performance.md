# Large-history resume performance in dev.14

This change reduces local work on a prepared task without changing source
admission, questions, cache keys, or the required-context contract. Schema 5 and
existing dev.13 judgments remain compatible. It does not require a full reindex
or a fresh semantic classification after upgrade.

## Implementation

- `semantic_cache.py` resolves cached items before computing provider batch sizes.
  Policy is checked around the cache pass and again at provider reservation and
  transactional publication. Source revisions are checked before lookup and
  before return. A disable or concurrent revision change cannot publish stale
  cached evidence.
- A server-confirmed single-source context rejection records its binary split
  under the exact parent judgment key. Subsequent recovery restores the split
  before mixing misses with newly arrived sources. This avoids resending a known
  oversized parent when its successful children are already cached. Partition
  hints retain source/context/question/model/lane/epoch dependencies, contain
  offsets rather than copied text, and are cleared by ordinary forget handling.
  They do not split judgments whose caller requires whole-context evaluation.
- `batching.py` checks whole-source fit first. `source_items` computes the full
  source hash once, rather than once per binary-search probe. Every character
  and source offset is retained.
- Source assessments group records by source instead of repeatedly filtering the
  full result collection. Task assertions share their source hash.
- `delivery.py` sizes entries against an exact invariant envelope overhead.
  Actual delivery still hashes pages and enforces the same byte ceiling.

No source-count, call-count, or request-size quota was introduced. All eligible
source revisions still pass through the cache. Initial judgment of a new task
scope can therefore remain expensive, and corrections can require many new
relationships even when old source judgments are reused.

## Evidence and reproduction

`scripts/live_resume_performance.py` copies an enabled store with SQLite online
backup, freezes transcript capture, and runs every dispatch in a separate Python
process. An optional baseline source tree compares the released implementation
on the same original request. It then selects a distinct task B, returns to A,
and adds a new correction. It verifies required sources, unrelated-task
exclusion, source immutability, complete brief reads, and exact event/revision
pairs actually sent for membership judgment. The fixture is disabled afterward;
the live profile, project pointer and product worktree are checked unchanged.

The frozen test is real Jev evidence. It is not independent Codex reasoning or a
Desktop UI timing measurement. The B request and correction are explicitly
synthetic additions to the real recorded corpus. A fresh selection also admits
later output that the original running turn excluded from its own input; this
is legitimate new evidence and is measured separately from old-source reuse.

Run with an enabled project and an explicit-selection baseline pack:

```sh
PYTHONPATH=src python3 scripts/live_resume_performance.py \
  --project /absolute/project \
  --baseline-pack PACK_ID \
  --baseline-tree /absolute/dev13-source-snapshot \
  --output-dir /absolute/private-result-directory \
  --required-source ORIGINAL_REQUEST_ID \
  --required-source COMPLETION_REPORT_ID
```

The baseline tree must contain its original `src/jcm`; it is not modified by the
benchmark. Full source texts and packs stay in the private output directory.
Only the sanitized aggregate should be published. Timing includes dispatch
through pack persistence; required-read timing and bytes are reported separately.

## Measured result

The [real-history aggregate](../evidence/resume-performance-dev14.json) passed
19 checks. All six phases returned normal/ready and completed required brief
reads without optional expansion. Warm source sets, task frames, and brief
evidence hashes were identical between dev.13 and dev.14.

| Same frozen history | Dispatch seconds | HTTP calls | Membership units evaluated |
| --- | ---: | ---: | ---: |
| dev.13 prepared A | 32.81 | 0 | 0 |
| dev.14 prepared A, first split-hint restoration | 4.18 | 0 | 0 |
| dev.14 prepared A, repeated | 2.86 | 0 | 0 |
| First selection of independent B | 91.68 | 188 | 863 |
| Return to A, including newly eligible tail | 5.16 | 4 | 24 |
| A after one new correction | 9.81 | 14 | 1 |

Repeated dispatch fell about 91% in this corpus. Return-to-A HTTP membership
requests contained no previously assessed event/revision pair. The correction
reused 881 membership units and 543 relationship units while evaluating its own
membership and 189 new relationship units. Related old assertions are still
necessary context for those new relationship judgments.

The first B selection remains expensive. This change establishes faster prepared
task resumption and delta processing, not low-cost first selection of every task.
The first performance candidate exposed resending of one known oversized parent
when mixed with new evidence; its failed run is retained privately, and the final
19-check run follows the partition fix. Regression verification passed 193 tests,
including disable/revision races during cached lookup and partition reuse after
adding a new neighbour. No required regression was replaced by timing assertions.
