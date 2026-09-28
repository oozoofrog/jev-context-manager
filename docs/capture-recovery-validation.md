# Capture and recovery validation — dev.11

This update addresses the real Desktop session where one 1,271,165-byte tool result stopped transcript catch-up while new hooks and work-list judgments continued.

## Implemented behavior

- Large admitted events use content-hash-addressed chunks and durable manifests. Capture commits storage before its cursor. Retry, corruption detection, shared-chunk retention, secret redaction and deletion remain enforced.
- JSONL reading spools large records instead of rejecting a line at 8MB. Decoding and later processing still materialize one record; this is not an unlimited-memory guarantee.
- Menus, selection, status and packs expose current capture state independently from Jev judgment quality. A blocked choice returns the original capture cause. Historical gaps are distinct from current blockers.
- Supported host `FunctionCallOutput` delegation is bound to its source, target and turn. Unsupported delivery cannot fall back to an older user request. Other tool outputs stay historical data.
- Exact copies of prior source text in tool outputs use references in recovery representations. Raw admitted blobs remain available; packs include referenced source records, including those otherwise excluded by semantic ranking.
- `status` is compact; `doctor` adds read-only diagnostics. `sync` catches up existing registered scope and processes an observed job frontier. Explicit JCM administration bypasses ordinary history dispatch. No persistent user mode or mandatory manual checkpoint is introduced.
- Schema v4 prevents an older runtime from silently misreading chunk manifests. Existing recording scope, disabled state, records and tombstones are preserved. Garbage collection and capture serialize reference publication through database writer locks.

## Verification lanes

| Lane | Result and boundary |
|---|---|
| Automated regression | 159 tests passed. Includes the actual failure structure, a valid >8MB line, interrupted chunk writes, corruption, redaction, deletion, request provenance, blocked menu/selection consistency, source reference closure and sync idempotence. |
| Real store clone | All 8 checks passed. A clone of the affected store advanced beyond the blocked offset and admitted 19 additional events. All event blobs reconstructed successfully; a repeat introduced no duplicate events or revisions. Live profile and project pointer remained unchanged. |
| Real Jev on cloned history | One successful real HTTP judgment produced a normal work catalogue. This is not evidence of Desktop work selection or animation quality. |
| Real CLI user workflow | Final candidate installed in isolation: 5 turns across 2 independent sessions, 18 checks and 19 successful real Jev calls. Bare skill → scope plus requirement → status only → natural-language sync → independent continuation all passed. Status-only added zero calls; the fresh result preserved both paused values, protocol 47 and an undisclosed random marker. See `evidence/capture-repair-dev11.json`. |
| Desktop work selection | Installed dev.11 passed the former blocked offset and linked the current host-delegated request. The target served/reassembled all 235 pages and compared current files without modifying product artifacts. However, 629/631 candidates were selected, including irrelevant diagnostic history. Retrieval precision therefore required the dev.12 follow-up; transport success alone was not accepted as task-selection quality. |
| Distribution | Existing release pipeline checks the wheel, bundled source, exact-commit CI, published installation and fresh skill loading, and backs up user installation/storage before replacement. Release execution is recorded separately. |

Private logs and full source-level evidence remain in `.task-notes/`. Public evidence contains only test results, counts and payload hashes. Live user transcripts are not shipped in the repository.

## Reproduction

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 scripts/live_sync.py --output-dir .task-notes/new-sync-run
PYTHONPATH=src python3 scripts/live_capture_repair.py \
  --project /absolute/project --session EXACT_SESSION_ID \
  --output-dir .task-notes/new-clone-run --with-jev
```

The live commands are opt-in and require existing credentials. The CLI workflow isolates its configuration, storage and workspaces; clone recovery leaves the original cursor/profile unchanged. Both retain the distinction between real provider transport, source delivery, actual continuation and product verification.
