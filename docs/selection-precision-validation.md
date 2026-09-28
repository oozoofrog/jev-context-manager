# Selected-task retrieval precision — dev.12

The dev.11 Desktop test repaired capture and exact request linkage, but selected 629 of 631 candidates and delivered 235 pages. A successful transport and a normal provider response did not establish useful task selection.

## Changes

- A separate applicability judgment distinguishes substantive task evidence, applicable shared constraints, unrelated history, and specifically uncertain connections. The selection code now uses this prerequisite before relevance or omission risk. High omission risk on another task no longer overrides its scope. Direct/shared/uncertain probabilities are composed against unrelated probability; an unrelated plurality below 0.5 cannot defeat their combined applicability. Explicit `omit` representations are honored; unassessed and specifically uncertain evidence remain conservative.
- The selected task's original assistant turn supplies historical context for short or generic source requests. The selected request and its original progress/completion reports remain protected anchors, with agent-reported provenance. They do not establish present product correctness.
- Capture order does not replace event chronology: material newly recovered from an older session remains eligible. A request's subsequent same-turn outputs are excluded from its own candidate input to avoid self-retrieval.
- Reference closure follows references actually present in delivered spans. A reference in an omitted span no longer pulls an unrelated full original back into the pack. Admitted raw blobs remain unchanged and available through source inspection.
- Recovery envelopes omit the full repository file inventory; the stored pack snapshot retains it. Per-span probability distributions remain in persisted Jev responses, while delivered records show compact applicability categories.
- No candidate count, daily call, or local request-byte quota was added. Every eligible source is judged using the existing provider-context subdivision path.

The applicability criteria follow TypeSafe's [typed Choice guidance](https://docs.typesafe.ai/primitives/choice). Model judgments are evidence for selection, not proof of applicability or authority to resume product work.

## Results

- Frozen affected-history comparison: all 11 checks passed with 163 successful real Jev calls. Selected text fell from 6,870,330 bytes (629 records, 235 pages) to 188,927 bytes (63 records, 17 pages). The original reference-image request and completion report remain present; three identified megabyte-scale diagnostic copies are excluded. Every raw event blob verified and all returned pages were served. No live profile or pointer changed.
- Independent CLI workflow: 5 turns across 2 independent sessions, 18 checks and 16 successful real Jev calls. Status-only added no calls, natural-language sync completed its observed frontier, and fresh continuation retained both paused values, protocol 47 and an undisclosed marker.
- Regression: 161 full-suite cases passed; the additional missing-turn guard and all 3 focused selection tests passed afterward. The release pipeline reruns the complete final suite before publication. Live sources in this comparison have explicit turn IDs.
- Installed Desktop continuation is checked separately after release; these clone/CLI results do not establish it.

## Acceptance boundaries

The original selected-task request is replayed on a frozen SQLite/blob clone with real Jev. The clone drops transcript registrations solely to hold the comparison corpus fixed. This test does not exercise capture, Desktop skill loading, or a new conversation. It checks original task anchors, specific unrelated source exclusion, raw-event identity and hash preservation, and complete page serving. The clone is disabled afterward; live profile and pointer bytes must remain unchanged.

`live_sync.py` separately checks natural-language status/update and an independent CLI continuation. The existing Desktop chat separately checks the installed release's selection, exact host-delegated request linkage, returned reading path and comparison with current files. Neither receiving all pages nor an agent's completion report proves current animation quality.

Results are recorded in `evidence/selection-precision-dev12.json` and the private `.task-notes/` logs. Release, installation, exact-commit CI and fresh loader evidence is recorded by the release pipeline. No private transcripts or pack text are published.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 scripts/live_selection_precision.py \
  --project /absolute/project --baseline-pack PACK_ID \
  --output-dir .task-notes/new-selection-comparison \
  --required-source REQUIRED_EVENT_ID --excluded-source UNRELATED_EVENT_ID
```
