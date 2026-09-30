# Current JCM implementation: correctness and consumption cost

Status: completed implementation and representative validation contract for
JCM 1.0.2, 2026-09-30. The seven prepared flows passed in both execution orders:
28 exact answers and all primary gates, with 14.0807% lower aggregate consumer
input. Uncached input increased by 1.1957%, so monetary savings are not established.
See [release notes](releases/1.0.2.md), the
[public validation summary](../evidence/continuity-consumer-1.0.2.json), and the
[single-planner delivery contract](context-delivery-design.md).
The implementation plan below is preserved as a completed engineering record;
it is not an active instruction to run additional experiments or modify user state.

## Objective and evidence boundary

JCM's product objective is effective context transfer between sessions, durable
preservation and reuse of work history, efficient use of Jev, and lower total
token consumption while continuing the correct work. Recover the right active
state for the current request: decisions, constraints, corrections, unresolved
work and the evidence needed to act. Smaller briefs or more passing tests alone
are not success. Do not replace a required fact with an ID that forces another
read, or reduce tokens by dropping necessary history.

The user explicitly selected Ponytail full and corrected the priority on
2026-09-30. Trace the real usage path, reuse existing code and prepared evidence,
and make the smallest change that improves that path. Do not expand validation
into all imaginable tool formats, malformed combinations or hypothetical use.
This priority supersedes broader exploratory H5 requirements in earlier reviews.

## Product acceptance priorities

The ordinary path is captured history → persisted source and reusable Jev
judgments → the current request's recovery → required context consumed in a new
or continuing session → correct next work. A fresh consumer with saved context
tests the recovery/delivery part; it does not by itself prove installed Desktop
hooks, capture or the entire product workflow. Keep those evidence boundaries.

| Product outcome | Evidence needed in this cycle |
|---|---|
| Resume across sessions and switch tasks correctly | Fresh-session recovery and A → B → A preserve current decisions, corrections, blockers and artifact identity without repeated user explanation |
| Preserve and reuse work history | Source and provenance survive the transition; unchanged-source reuse and changed-question recovery use the appropriate existing evidence |
| Use Jev and tokens effectively | Report Jev preparation and reuse calls/input/output, then actual consumer input/cached/output including extra reads and retries, alongside correct answers and latency |

Use the existing prepared seven-stage corpus for these representative flows;
do not create another acceptance corpus or enlarge its edge-case matrix. Keep
local source/policy/forget/failure regressions where relevant, reusing unchanged
passing evidence. Add a test only for a changed real path, a reproduced defect
with material impact, or a data-integrity/security boundary. Exhaustive diagnostic
tool compatibility is not a prerequisite for demonstrating product usefulness.

Blocking comparison conditions are incorrect/missing current context, lost or
misbound history, invalid policy/source/runtime, incomplete required reading or
model exposure, incorrect answers, incomplete response usage, and failed actual
consumer execution. These prevent a trustworthy result and remain hard gates.

Counts of failed shell commands and generic tool-format support are secondary
diagnostics. A counter that cannot attribute an output safely must report the
metric as unavailable/unknown, preserve the issue, and exclude it from claims.
It must not alone stop an otherwise valid context/answer/token comparison.
Actual required-read, exposure, process, policy and usage failures still stop it.
Do not relabel a failed diagnostic test as passing or weaken trust-boundary checks.

Preparation is not free because it was reused from an archive: report its saved
Jev cost separately from this run's incremental calls. Report first recovery and
unchanged-source reuse separately. A before/after runtime pair demonstrates that
specific improvement, not JCM-versus-no-JCM savings or universal usability. Obtain
one valid representative pair before expanding the experiment; retain the existing
conditional reversed confirmation for a positive efficiency claim.

Existing code, synthetic native fixtures and regression evidence are available.
The deleted RunnersHeart corpus is neither required nor to be restored or
recaptured. Historical costs remain historical; new measurements stand on their
own input and source hashes. The new work must not report recovery of the old
7,230-event experiment or claim equivalence with its coverage.

The starting runtime is
`042c62d686a640db376ea4b656999d3ed4d2f0a69ea0dc4c0210220d3fe7d213`.
Freeze its source, bundle, test and harness bindings before implementation changes.
Preserve the dirty checkout. Use an isolated immutable baseline runtime for
evaluation rather than a Git reset or a worktree that loses uncommitted changes.

## Findings that guide the first iteration

The latest saved native comparison passed its answer oracle but consumed 394,575
baseline versus 501,638 candidate input tokens. It compared full delivery against
brief/delta within one implementation; it is not the before/after comparison for
the new work. In the first stage, baseline used 3 reads and 83,092 input tokens;
candidate used 6 reads and 162,025 input tokens. Those are observed differences,
not proof that one mechanism caused the entire regression.

Architect audit also confirmed a request mismatch in the old native harness:
`live_native_delivery.py` dispatches the short stage request, then appends
`consumer_prompt` only when creating the consumer case. The saved cold pack's
query asks to continue Harbor and state its blockers/constraints; the consumer
additionally asks about protocol and whether the old Orchard failure applies.
Both lanes look up Orchard and the candidate expands additional hits. That extra
question was not part of the planner's explicit request. Do not attribute all
of those reads to a runtime selection defect or teach the runtime a hidden oracle.

First trace each extra lookup, expansion and reread back to its requested fact,
available required evidence and uncertainty. Distinguish:

- A required fact/qualification omitted by the planner: repair selection or
  evidence completion in the owning planner.
- Necessary evidence delivered but difficult to interpret: repair deterministic
  presentation and provenance, preserving the frozen contract.
- An unknown scope that genuinely prevents a conclusion: resolve its boundary
  through evidence or preserve a specific unresolved outcome. Never invent an
  affirmative answer to avoid a read.
- Repeated delivery, stale retained state, or harness artifacts: repair the
  relevant read/delta/harness behavior and retain honest usage accounting.

This diagnosis, with concrete examples, precedes a new threshold or prompt change.
Do not add a second relevance selector, a parallel memory framework, arbitrary
production request caps, or a free-form summary that replaces exact evidence.

## Architecture requirements

1. The current-question planner owns the required evidence set. Complete exact
   source spans, parent conditions, applicable corrections and conflict endpoints
   before freezing it. All later formats preserve that set.
2. The consumer's first required view carries the current artifact identity,
   answer-relevant facts and their limits, with compact exact supporting evidence.
   Factor shared provenance deterministically. Audit probabilities and decision
   inventories are optional unless necessary to interpret the current state.
3. Qualification scope and proposition uncertainty are distinct. An unresolved
   scope may be the correct answer to a bounded question; label it explicitly.
   If the requested action depends on resolving that scope, the pack must not
   imply that the action is established or authorized. Receipt completion and
   `ready/normal` must not erase semantic incompleteness.
4. Optional exact-source expansion remains available. Reduce unnecessary reads by
   making required information sufficient and clear, not by disabling lookup or
   instructing the consumer to avoid evidence needed for correctness.
5. Delta is an encoding of the same evidence, usable only with a retained exact
   base. Task switches, source invalidation, forget, policy withdrawal and context
   loss preserve their existing rejection/full-recovery behavior.
6. Reuse successful judgments under the existing semantic dependency contract.
   Unchanged-source warm reuse and recapturing a new request are separate cases.
   New-question applicability must use the current question even on the same task.
7. HTTP 402 stops the current operation; a fresh operation can retry after credit
   returns. Preserve prior error/usage evidence. Keep source journals and fixture
   artifacts after disabling capture so future evaluation can be reproduced.

## New evaluation contract

Use fictional software artifacts and synthetic native transcript shapes. No
private or deleted historical content is needed. Implementation owns development
fixtures; independent validation freezes separate acceptance inputs, expected
facts, forbidden inferences and source-span witnesses before inspecting candidate
answers. Record fixture/oracle hashes. Fix an invalid fixture with a documented
reason and rerun both sides; never adjust it merely to make a candidate pass.

The following cases describe relevant coverage, not a demand for a fresh live
run of every combination. Reuse the already prepared representative sequence and
existing local evidence; add coverage only under the product priorities above:

| Case | Required observation |
|---|---|
| Exit 0 and a success report with an actual body blocker | Preserve blocker and unresolved report conflict |
| Relevant scalar in nested/escaped JSON and long prose | Preserve exact text, identity, status and a late exception |
| Partial correction | Replace the corrected clause and retain unchanged constraints |
| Same A/B labels in unrelated artifacts | Keep concrete artifact identities separate |
| Same task, narrow question versus broad continuation | Change required scope without losing applicable conditions |
| Missing or uncertain evidence | State the unknown; never invent completion or permission |
| A → B → A with an intervening correction | Retain correct task state and delta/base behavior |
| Warm request over an identical source snapshot | Verify actual reuse without silently adding source events |
| Unrelated bulk records and copied wrappers | Exclude irrelevant material only after valid assessment; preserve selected references |
| Source revision, policy change, forget and 402 | Retain local invalidation and failure regressions |

Use a small development set and an independently authored acceptance variant.
Make the smallest cases fit a single page so missing evidence and extra reads are
diagnosable. Exercise pagination and encoded strings separately, then scale the
corpus only after the small correctness and consumption comparison passes.
Synthetic coverage is measured case coverage, not historical or universal recall.

## Fair comparison and cost gates

The primary comparison is **starting implementation brief/delta versus improved
implementation brief/delta**, with the same new source corpus, question sequence,
oracle, consumer prompt/tool affordances, model, reasoning and service settings.
Full-source delivery can be a secondary diagnostic lane, not the primary baseline.

Configuration stability covers the settings actually used by the comparison.
Freeze explicit model/reasoning/service-tier values and the isolated consumer
policy/roots; verify them before paid execution and retain native observations.
The global file's full hash is diagnostic provenance, not an acceptance gate
for unrelated fields that are never copied. A relevant setting or effective
policy change still stops the run. Preserve the user's current global file.

For the observed repair-3 stop before the last candidate stage, retain the 13
completed stages and resume only that unexecuted stage in the same candidate
session after an implementation-owned, validated continuation handoff. Bind the
original case, reader, runtime/source and completed raw/native evidence; retain
the original incomplete result and write a separate derived result. Reconstruct
response/turn identity state so prior usage is neither lost nor counted again.
A resumed result must identify its interruption; it is not a new fresh pair.
If the preserved state cannot establish continuity, report the exact gap before
launching further paid work. No general restart framework or speculative matrix.

Each lane binds to its own frozen runtime, profile, source snapshot and pack. In
particular, optional lookup/source expansion must not route both lanes through
the candidate runtime or a shared mutable pack. Preserve stable source identity
where appropriate and verify each lane's actual source binding. A changed harness
must apply identically to both lanes and be recorded separately from runtime gains.

Use one canonical semantic request per stage for both dispatch and consumption.
Every requested fact, comparison and relevant output-field meaning must be in
that request before planning. Delivery instructions may differ from the semantic
request, but must not append new answer requirements afterward. Record and assert
the planner-request and consumer-task-request hashes. A consumer intentionally
asking a new question is a separately labeled query-change case that replans.
If a case explicitly asks whether an older artifact affects the current one,
evidence establishing that relationship or independence is answer-relevant; do
not simultaneously declare all such evidence forbidden merely for being old.

Separate source preparation cost, exact required delivery size, all actual reads,
and end-to-end consumer cost. Capture every response usage once; cached input is
a subset of total input. Include lookups, expansions, retries, rereads, output and
latency. The expected answer is withheld from the consumer. Required evidence must
actually appear unabridged in model-visible outputs; writing pages to files is not
consumption. Do not hide failed answers or favorable/unfavorable runs.

Before live calls, reuse the inventory of units, cache hits/misses and request
sizes when its inputs are unchanged. Run affected correctness and essential
source/answer/usage checks; secondary diagnostic failures are reported separately.
A material unexpected fan-out or context rejection triggers diagnosis before scaling. There
is no quota-based omission of input or production request-count limit.

Run one small matched live comparison. If both sides are correct and the candidate
improves total input, confirm with a second matched comparison in reversed lane
order to check model/cache-order variability. If it fails correctness or regresses
cost, preserve the result and return a concrete finding to implementation/design
before enlarging the experiment. This staged experiment is not a production cap.

For an efficiency acceptance claim, required/forbidden facts must pass, both
matched confirmations must reduce aggregate total input, and a correctness-critical
case may not silently regress. Report per-case exceptions, preparation tradeoffs
and latency separately. A single positive run is exploratory evidence. A change
that fixes correctness but increases cost may be accepted only as a correctness
repair with efficiency still open; do not label it the completed efficiency goal.

## Ownership and iteration

The required sequence is **design → implementation → independent validation →
design decision or implementation repair**. The architect owns this contract,
acceptance criteria, scope changes, prioritization and review of conflicting
results. The implementation chat owns all executable code: runtime, tests,
shared harnesses, archive restoration, preflight, run orchestration, metering,
validation tools and bundle synchronization. A file under `validation/` is still
implementation work when it creates or changes executable behavior.

The validation chat owns independent fixture and expected-answer data, its
acceptance plan, source-binding inspection, execution of the fixed tools and
evidence-backed judgments. It may inspect source and existing artifacts with
read-only commands. It does not author or repair executable tools, patch product
or test code, create a workaround execution path, or tune expected answers to
hide a failure. If a needed tool is missing or defective, validation records the
input/interface requirement, expected versus actual behavior, reproduction
evidence and impact, then returns it to implementation. Questions about the
contract or scope return to design. Every completed or blocked work unit also
reports to design as required below.

Implementation must review, test and own previously validator-authored helper
code before it can be used for acceptance. Preserve the original helper and
failed-run evidence; copy accepted code into an implementation-owned path.
Keep independent expected answers separate from that code handoff and use
development fixtures for implementation checks. The ready manifest must bind
all executable tools used by validation as well as the candidate, with file
hashes, local check results and `writing_stopped=true`.

The user explicitly requires each chat to report completion directly to the
design chat. After saving a completed work unit's evidence and final manifest or
result, send a concise report to the design chat with `send_message_to_thread`.
Use the chat identities in `architect-handoff.json`; no new report chat is needed.
This applies to implementation and independent validation, including an outcome
that is partial, failed or blocked. Backfill any already-completed result that
has not yet been sent. This requirement supersedes earlier instructions saying
that no reply to the architect was needed.

Include the result, actual changes or checked behavior, source/harness/oracle
bindings as applicable, checks run versus unrun, absolute evidence paths, observed
costs and remaining blockers or design decisions. Distinguish local checks from
real provider/consumer validation and release evidence. Send once per completed
work unit or material final correction, not on unchanged progress. Report delivery
does not itself establish acceptance: the architect reviews the saved evidence
and assigns any necessary follow-up within the active JCM scope.

Both chats reuse the existing project checkout. During implementation, validation
waits for the complete handoff; it may preserve evidence, inspect prior results
and prepare its plan or independent data in its assigned evidence directory.
Independent test execution, archive restoration and paid runs start only after
the implementation ready manifest is written, all relevant writes stop and the
bound file hashes match. Implementation performs its own local checks before
that handoff. Validation then runs the fixed tools without modification. On a
defect, preserve the failing evidence and return to the appropriate earlier stage;
do not repair and continue inside validation. An implementation fix produces a
new manifest and changed file hashes, invalidating only affected results. A
passed validation returns to design for review of the saved evidence.
Large jobs remain serial. Do not change global configuration or perform an install,
commit, push or release as part of this work.

Use `.task-notes/current-improvement-20260930/` for the new cycle:

- `architect-handoff.json`: baseline bindings, active scope and chat IDs.
- `implementation/diagnosis.md`: trace-backed causes and chosen repair.
- `implementation-ready.json`: baseline/candidate hashes, changed semantics,
  local/package checks, fixture/harness interface, and `writing_stopped=true`.
- `validation/acceptance-plan.json`: independent fixture/oracle hashes and gates.
- `validation-result.json` and `validation-report.md`: matched correctness and
  usage, explicit reuse/fresh-run distinction, failures and remaining scope.

The old `historical_resume_ready=false` no longer blocks this cycle. No attempt
to recreate that old corpus is part of the new objective.
