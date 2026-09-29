# Context reuse in dev.13

This is the dev.13 validation record. The subsequent question-specific delivery
layer is documented in [Current-question context](query-context.md).

Sources in the journal remain canonical. Schema 5 adds rebuildable source indexes,
independent semantic judgments, task views, assertions, relationships and evidence
representations. It does not change the capture scope or require manual checkpoints.

## Ownership and invalidation

| Module | Responsibility |
| --- | --- |
| `classification.py`, `worker.py` | Query-independent source classification shared by background processing and recovery |
| `semantic_cache.py` | Cache keys include exact item/context, source revision, question body, rubric, model, policy epoch, and provider lane; source pairs carry both dependencies |
| `local_index.py`, `reusable_selection.py` | Unicode/path candidates and independent task-membership judgments; widening preserves unassessed sources and no-match synonyms |
| `task_state.py` | Source-backed task routing, assertion spans, proposed relationships, guarded review and consistent task-view publication |
| `representations.py` | Versioned brief/detail/full exact-source representations; uncertainty falls back to source material |
| `delivery.py`, `coordinator.py` | Immutable, independently paged views; brief completion is independent of optional audit reads |
| `source_read.py` | Paginated source reads and complete revision-specific evidence receipts |
| `metrics.py`, `provider.py` | Observed transport, provider usage, reuse, delivery bytes and stage timings |

Independent source judgments can survive batch reordering and new neighboring
items. Query-dependent membership uses the canonical task scope. Rephrased work
reuses a task only after a narrow identity judgment; uncertain or multiple matches
expand recovery. The index currently contributes observable candidates while all
eligible source revisions still pass through the independent cache. It is not a
claim of sublinear cold retrieval.

Assertions retain exact offsets and basis. User text is split into exact sentences;
other source text uses paragraphs. Tool payloads remain context/verification evidence;
quoted requirements or corrections in a tool result do not become new user
authority. Correction candidates originate from user assertions. Raw tool reports do not
resolve an open issue; the tool source remains available as evidence. Classification categories are inherited from
source assessments, not a separately verified fact extraction. Repeated identical
sentences within one block retain every occurrence offset. They are not deduplicated
across independent events.

A correction or resolution candidate compares the exact older and newer assertions,
including surrounding conditions and dependencies. Uncertain precedence preserves
both and marks affected assertions disputed. A whole-assertion relationship can be
confirmed only after the exact original sources have been fully served and the
current task view, user frontier, revisions, and model/rubric still match. This is a
scoped review receipt; it does not establish implementation or test success.

## Delivery and measurements

The required brief contains the goal, assertion status/source references, unresolved
relationships, and selected evidence excerpts. Detail/full material and audit payloads
are optional. A full materialized source can contain compacted references; `inspect
--raw` provides raw source access. Source IDs, hashes, offsets, creator and version
connect representations to their origin. There is no additional summary model call.

A served receipt means the CLI returned bytes. It does not prove those bytes were
consumed or used by a host model. Provider calls/usage, required/optional delivery,
source expansion, and the independent-session output checks are separate evidence.
Selection/state duration ends before pack assembly. Pack preparation and persistence
are measured separately; the recorded dispatch boundary is immediately before commit.

Forget, disable, policy changes, and source revision changes outrank cache reuse.
Publication validates the request's input frontier, excluding its own later non-user
output while still detecting new user messages and other sessions; concurrent changes fail the attempt
instead of presenting a stale normal pack. Related-file snapshots are reconciled
against current files without promoting historical verification reports.

## Acceptance evidence

- Fixed Korean corpus, real Jev, cold → rephrased → current correction:
  [aggregate](../evidence/context-reuse-dev13.json). Twelve checks passed, including
  common constraints, an unrelated task, a late exception, preservation of an
  unchanged protocol clause, and required reads without optional audit.
- The fixed-corpus sessions are separate storage connections/session IDs, not
  independent Codex processes. The independent CLI acceptance is
  `scripts/live_context_reuse_cli.py`; [22 checks passed](../evidence/context-reuse-cli-dev13.json)
  across three Codex sessions, including executable output and a current correction.
  Installed Desktop use is verified separately.
- Regression cases in `tests/test_context_reuse.py` cover independent caching,
  task scope, source/policy/model/rubric invalidation, relation review, partial
  corrections, concurrent mutation, no-match expansion, and pagination receipts.

The fixed-corpus repeat used 1 transport call and 5,338 request bytes, compared with
5 calls and 55,323 bytes cold. Old source reassessment was zero. The correction used
5 calls and 118,057 bytes with 20 new relation units, so this example establishes
repeat reuse, not a general reduction in every request or total workload cost.

## Resume performance

The dev.14 cache traversal, adaptive partition reuse, and delivery sizing changes
are documented in [large-history resume performance](resume-performance.md).
They preserve existing source judgments and the schema-5 storage contract.
