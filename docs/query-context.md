# Current-question context

JCM now separates reusable task knowledge from the evidence needed to answer the
current question. A request to resume implementation, explain only an exception,
or diagnose one failure can reuse the same task identity without reusing the same
delivery selection.

## Responsibilities

- `task_state.py` routes by substantive work, independently of the requested focus
  or output depth. It still widens recovery when task identity is uncertain.
- `reusable_selection.py` derives task membership from source applicability.
  Low relevance to the original task question cannot hide a member from a later
  question. Source classification and membership retain their independent caches.
- `representations.py` builds exact-source representations and a reusable
  preservation floor. User statements remain complete. For other sources, Jev
  distinguishes unique constraints, qualifications, corrections, outcomes and
  open issues from optional diagnostic detail. Uncertainty preserves the source.
- `query_context.py` separately checks question equivalence and selects source
  detail and paragraph evidence for that question. The task frame is built before
  question selection and is never replaced by that smaller selection.

Question equivalence requires matching information needs and depth, not task
membership alone. A separate referent judgment prevents reuse of questions about
the latest error or a relative source position when the source context changes.
The current request remains visible alongside the canonical equivalent question.

Each source can contribute brief evidence, supporting detail, its complete text,
or nothing to the default delivery. Task-wide mandatory spans override omission.
Sources involved in correction relationships, referenced originals, selected task
anchors and unassessed sources are preserved. Membership span boundaries also
limit question selection, so a mixed-topic source cannot reintroduce an excluded
span or its compacted references.

For a brief, both `support` and `omit` mean deferral. Their probabilities are added
before applying the action threshold; disagreement between these two labels is
not uncertainty about deferral. For detail, only `omit` permits deferral. A
possible missing core fact is preserved. Qualification preservation uses a
separate judgment, rather than inferring safety from query relevance alone.
Source detail levels are nested: the smallest level covering at least 0.8 of the
probability mass is delivered. Disagreement between brief and detail can thus be
covered by detail, while a plausible need for full text retains full text.

Correction comparison also checks the target property using the two exact
assertions supplied as structured question data. A confidently different property
cannot dispute an unchanged clause merely because a nearby clause was corrected.

`selected_records` describes the current question's exact selected spans;
`task_records` retains the reusable task evidence in the audit view. Optional
detail/full reads include task sources omitted from the required view. Source
hashes, offsets, basis and expansion commands remain attached. No prose summary
is generated, and historical reports never become current verification.

## Caching and storage

Question profiles use task identity, source binding, model, rubric, policy epoch
and provider lane. Equivalent questions can reuse per-source selection judgments;
changed focus creates a separate selection context. Per-source judgment keys also
include the source revision, complete question definition and canonical query.
Deterministic preservation guards are reapplied on every dispatch, so a newly
disputed source cannot vanish because of a cached optional selection.

Schema 6 adds `query_views`. Opening a schema 5 store preserves its journal and
adds the new derivative table. Forgetting removes profiles along with the other
derived state. Earlier runtimes reject schema 6 rather than forgetting a session
while leaving these derivatives behind. Current-question and canonical-question
source revisions are also delivery dependencies; a changed request invalidates an
old pack. The installed user store was not migrated during this work.

## Validation — 2026-09-29

| Local validation | Result |
| --- | --- |
| Full regression suite | 211 passed |
| Question-context tests from the built wheel in an isolated environment | 12 passed |
| Wheel runtime compared with canonical source | All 34 Python files matched |
| Bundled plugin compared with canonical source | Exact match |
| Plugin manifest validation and `git diff --check` | Passed |

Logs are in `.task-notes/query-context/`: `full-tests-verified.log`,
`wheel-tests-final.log`, `wheel-final.log` and `wheel-verification.json`.
The full suite emitted the previously observed non-failing SQLite
`ResourceWarning`; it is retained in the log.

The real Jev fixture runs implementation resumption → exception-only explanation
→ equivalent rewording → detailed failure analysis → a new correction. All
**13 acceptance checks passed** with `jev-1.13.0`.

| Request | Diagnostic source delivered | Characters | New query-source judgments |
| --- | --- | ---: | ---: |
| Resume implementation | Detail | 3,094 | 1 |
| Explain the exception | Brief | 146 | 1 |
| Equivalent exception question | Reused brief | 146 | 0 |
| Diagnose the failure with the exact trace | Full | 3,094 | 1 |
| New correction and exception question | Brief | 146 | 1 |

The first focus change reused all three existing membership judgments. Later
stages also encounter previous procedural questions as newly admitted history;
these can require membership judgments. Equivalent-query reuse does **not** imply
zero HTTP calls: routing, classification and newly admitted records still incur
work. This fixture used 6 / 4 / 5 / 5 / 6 HTTP calls across its five stages.

The exception and equivalent-question deliveries retained the unique failure
status, pause exception and unverified-device limitation while deferring the
trace. Detailed diagnosis restored the exact callback sequence and values. The
new correction preserved 8 seconds and protocol version 1, and marked the prior
5-second requirement disputed without declaring it implemented.

- Reproduce with `PYTHONPATH=src python3 scripts/live_query_context.py --output-dir PATH`.
- The portable [aggregate result](../evidence/query-context.json) contains checks,
  stage metrics and evidence boundaries.
- Full local packs, read receipts and provider diagnostics are in
  `.task-notes/query-context/live-verified/`; the fixture is disabled after the run.
- The existing `scripts/live_context_reuse.py` scenario also passed all **12**
  checks, including compact logs and preservation of the unchanged protocol
  clause. Its evidence is in `.task-notes/query-context/live-reuse-v3/`.
  A comparison run on the unchanged dev.15 source is preserved in
  `.task-notes/query-context/live-reuse-baseline/`.
- Deterministic regression cases in `tests/test_query_context.py` cover changed
  focus, equivalent requests, new corrections, lost anchor relevance, provider
  failure, uncertain equivalence, revision invalidation, relative references,
  preservation overrides, action probabilities, cross-property corrections,
  forgetting and schema migration.
- Earlier live attempts and failed regression logs remain in
  `.task-notes/query-context/`. They exposed task/focus conflation, overbroad
  preservation judgments and incorrect handling of equivalent deferral outcomes;
  they are not counted as passing evidence.

This is a synthetic-corpus selection and delivery test. It does not measure a
downstream generated answer, an independent Codex session, Desktop consumption or
the user's live history. The change is in repository source and the bundled
plugin; no new release or user installation was performed.

All eligible source revisions still pass through the membership cache. This work
does not claim sublinear cold retrieval or a learned cost-versus-utility policy
for choosing partial versus complete reconstruction. No request-count, byte-size
or candidate-count quota was introduced.
