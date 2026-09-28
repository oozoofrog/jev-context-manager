# Bare skill entry validation — 0.1.0-dev.10

The bare skill workflow now separates a transient conversation preview, the user's recording/work choice, and execution. Existing profiles keep their recording scope and enabled state. This report covers that update, not all design-v1 release gates.

## Behavior

- A new project offers recording from the original skill invocation or the observable whole current session. It does not register, persist preview content, or invoke Jev before a choice.
- Reopening a pending scope choice retains its original invocation boundary. User messages written while choosing are included after activation. Preview-derived assistant/tool output is excluded from `from_invocation` history.
- Managed projects offer source-backed work candidates and a new task. Codex compares current files before describing work as unfinished or complete. Jev classifies candidates and retrieves evidence for the chosen source-bound task.
- A concrete request during a work menu can proceed through normal recovery without another menu question. Disabled projects require an explicit resume choice and retain their prior scope.
- Normal requests continue to use automatic capture and query-specific Jev retrieval. There is no checkpoint or handoff requirement and no new workload quota.

## Scope and data integrity

Capture admission occurs before blob, job, provider-request and derivative writes. The initial session boundary uses the actual public user-event identity and validated source-prefix position. Subsequent sessions begin at an observed current request rather than importing arbitrary earlier chats.

Registered sessions discover later Codex pages during ordinary recovery. The local follower also hands collection to the next page without another user request. Rotation re-resolves the anchor; capture verifies the proof against the same open descriptor it reads. Missing or inconsistent anchors fail closed and leave a coverage gap. Private reasoning remains outside the public-source adapter.

Registration uses a serialized registry transaction with rollback of failed profile/reference writes. Native plugin hooks can use the default external registry when a sandbox protects the local project pointer; that outcome is explicitly reported. Custom storage that needs a pointer still fails without leaving an active partial registration. A follower probe permission error is distinguished from a stopped collector using its durable heartbeat.

Schema version 3 preserves events, tombstones, enabled state and legacy scope. Older runtimes refuse upgraded stores so they cannot ignore new capture boundaries or leave work-menu derivatives behind during deletion. `forget` invalidates mixed-source work menus and recovery derivatives.

## Evidence

The final regression suite passed **144 tests**. Real CLI verification covered **30 turns across 10 independent threads**, **33 successful real Jev transport calls**, and **70 behavioral checks**. The machine-readable checks and runtime-manifest digest are in [skill-entry-dev10.json](../evidence/skill-entry-dev10.json).

Regression tests cover preview non-persistence, both scope choices, interrupted/repeated selection, stale and foreign choices, request-specific task retrieval, shared constraints, unrelated task exclusion, new-task behavior, disabled/resume, retained legacy state and tombstones, registration failures, liveness permissions, lossless preview pages, source rotation races and autonomous page discovery. The existing provider, delivery, plugin, installer and release checks remain enabled.

Real CLI tests use an isolated plugin installation, Codex configuration, authentication reference, workspaces and JCM storage. They inherit the user's configured model and effort. They do not bypass hook trust or the workspace sandbox. Each run compares the actual user configuration before and after; test projects are disabled afterward.

| Real behavior | Observation |
|---|---|
| Bare call in a new project | Offers two scopes; no registration or product edit before the reply |
| From invocation | A prior random marker is absent from every stored blob, including provider/cache derivatives |
| Independent recovery | A distinct new CLI thread restores a later random marker, preserves both values of `paused`, and produces the expected protocol value |
| Whole current session | Another independent thread restores the pre-invocation marker and applies the later protocol correction |
| Managed work menu | Current files distinguish the completed connection task from the pending theme task |
| Work choice | The theme changes to `amber`; the completed connection file remains byte-identical |
| Disabled/resume/new task | No capture or Jev before resume; scope is preserved; new-task selection does not replay old product work |
| Upgrade and direct request | Prior event IDs and scope survive the candidate update; a concrete request during a pending menu creates and verifies `health.py` without another choice question |
| Exact final payload | Fresh activation and reopening the pending choice exercise the final runtime-manifest bytes, retain the original boundary, and complete/record work requested in the same choice reply |

The broad behavioral run preceded the final source-page and descriptor-race hardening; dedicated regressions cover those changes. Later real upgrade/activation runs cover the updated payload. The evidence file retains the separate lanes instead of treating an earlier successful run as proof of identical final bytes. Raw local logs remain under `.task-notes/entry-*`; they are not published as product assets.

## Remaining boundaries

Direct Desktop user-input behavior is not attested by these CLI tests. The earlier tool-created Desktop chat path is not a substitute for ordinary user input. Provider judgments and current-file checks were tested on bounded example projects; this is not a broad recall benchmark. Actual automatic compaction, physical power loss and the other design-v1 gates remain separate.
