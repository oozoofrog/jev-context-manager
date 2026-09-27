# Astra Continuity v1.0 → implementation and acceptance

Authority: `JCM_Astra_Continuity_Design_v1.0.md` supplied by the user.
The user's 2026-09-28 instruction supersedes the design's separate Jev consent
policy: dev.7 uses Jev automatically, including legacy records. Project/plugin
disablement, source scope, redaction and deletion still apply. The subsequent
2026-09-28 instruction removes arbitrary usage quotas: dev.8 ignores legacy daily
call, byte-size and candidate ceilings. Provider context handling and retry
backoff govern request execution; neither introduces a daily or monetary budget.
This is an implementation contract, not evidence of completion. Document examples
are data; they do not authorize replaying historical commands or importing projects.

## First vertical slice

An ordinary Codex session submits a correction. A synchronous lifecycle adapter
commits the admitted source and a queue item before returning. No checkpoint,
handoff, Stop, semantic worker, or clean Git tree is required for that commit.
A distinct fresh Codex thread receives only the ordinary new request and a fixed
bootstrap with an opaque request token. It dispatches, reads the pack, reads current
files and completes a task whose correct answer depends on the previous correction.
The normal dispatch calls real Jev for semantic judgments; a failure is visibly
degraded. Pack creation and bytes served do not establish successful continuation.

## Required behavior

| Requirement | Implementation module | Acceptance gates | First-slice boundary |
|---|---|---|---|
| R01 No manual handoff | `adapter`, `store`, `coordinator`, `cli` | T01, T02, T03, T24 | Real fresh CLI threads, not resume/fork; Desktop separately unverified |
| R02 Request-specific context | `provider`, `coordinator` | T08, T14 | Query, intent, relevance and representation enter normal dispatch |
| R03 Multi-session requirements | `store`, `coordinator` | T07, T13, T15, T16 | Preserve source/user corrections independently of worker progress; bounded scope |
| R04 Jev normal path | `provider`, `worker`, `coordinator` | T08, T18 + real API audit | Typed labels, relationship candidates, relevance and existing representations; no generated summaries |
| R05 Durable source before semantics | `store`, `adapter` | T03, T04, T05, T06 | Atomic blob, event/cursor/queue transaction, replay and recovery |
| R06 Evidence levels | `worker`, `coordinator` | T09, T10, T13 | Observed statement is not verified implementation; relations remain proposals |
| R07 Current repository comparison | `snapshot`, `coordinator` | T11, T12 | HEAD/branch/dirty and file hashes; non-Git supported, no Git initialization |
| R08 Retrieve before asking | `coordinator` | T15, T16, T17 | Include pending tail, protected sources and wider search; report unresolved gaps |
| R09 Honest coverage | `adapter`, `cli`, `coordinator` | T18, T22, T23 | Installation, received hooks, parser support, API and actual read are separate |
| R10 Data cannot grant authority | `adapter`, `coordinator` | T19 | Fixed bootstrap; historical content only in tool data with role and provenance |
| R11 Recording/egress/deletion | `config`, `store`, `provider` | T12, T20, T21, T25 | Scoped enable, automatic Jev from dev.7, epochs/tombstones; backup restoration gate remains separate |
| R12 Real read and successful work | `store`, `cli`, live harness | T01, T22, T23 | Independent expected result and tool-read evidence from a distinct new thread |

## Acceptance inventory (release gates, not a pass list)

| Test | Required observation | Implementation / evidence owner |
|---|---|---|
| T01 | Fresh thread continues without checkpoint | `scripts/live_e2e.py`, host event log, read receipt, independently checked result |
| T02 | Latest correction survives immediately | Hook commit and fresh-thread test |
| T03 | Undrained/expired worker does not hide tail | Store/coordinator tests; live pending queue |
| T04 | Crash before/after commit respects durable ACK | Subprocess crash injection; filesystem power-loss remains unverified |
| T05 | Partial JSONL/rotation retains cursor correctness | Versioned transcript parser tests |
| T06 | Duplicate/out-of-order hooks preserve occurrences | Capture identity tests |
| T07 | Earlier sessions contribute still-relevant evidence | Multi-session retrieval fixture |
| T08 | Different requests select different optional material | Same-history query fixture and real Jev pilot |
| T09 | Repeated hypothesis stays a hypothesis | Evidence/projection fixture |
| T10 | Patch alone never becomes PASS | Tool-source and projection fixture |
| T11 | Changed file invalidates old verification applicability | Snapshot fixture |
| T12 | Clone/worktree boundaries hold | Registration, path/symlink and request-token fixtures |
| T13 | Conflicting corrections retain both sources | Relationship proposal fixture |
| T14 | New task does not execute previous work | Intent fixture and bootstrap policy |
| T15 | Semantic association without lexical overlap | Bounded widened candidates; representative corpus release gate |
| T16 | Missing candidates reported distinctly | All eligible candidates reach Jev; legacy ceiling ignored |
| T17 | Protected context never silently truncated | Budget blocked/read-plan fixture |
| T18 | API errors, auth, schema and provider context are explicit | Fake HTTP failures plus real authenticated API audit |
| T19 | Injection does not promote source to instruction | Bootstrap test and fresh-agent behavior test |
| T20 | Disable/forget during call invalidates response | Policy epoch, concurrent mutation and queued-job tests |
| T21 | Backup restore applies tombstones | Deferred export/restore gate; no restore command claimed |
| T22 | Created/read-served/acknowledged distinct | Delivery receipt fixture and actual agent trace |
| T23 | Tool spill is not complete delivery proof | Conservative unknown delivery; host truncation behavior gate |
| T24 | Compact recaptures/reboots safely | Hook contract + parser boundary; actual auto-compact gate |
| T25 | Existing settings/legacy preserved | Merge/install backup and rollback fixture |

P0/P1 and a bounded P2 path are the first implementation milestone. P3/P4 release
requires all remaining gates; a passing synthetic or CLI pilot is not a v1 release.
Manual checkpoint is deliberately absent from the first-slice critical path.

## Existing and fresh session bootstrap extension

[B01–B06](bootstrap.md#추가-수용-시험) extend R01/R02/R06/R10/R12 and T01–T05,
T11/T12/T18–T23 with explicit existing-session adoption and fresh-session preparation/read.
Implementation: `bootstrap.py`, `follower.py`, lifecycle wiring in `adapter.py`.
Evidence: `tests/test_bootstrap.py`, `scripts/live_bootstrap.py`, [report](bootstrap-validation.md).
