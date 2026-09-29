# Recovery on large recorded histories

Recovery keeps source records authoritative while reusing completed judgments.
Installing a new runtime no longer discards semantic work merely because the
project policy epoch changes. Source revisions, the question and its context,
model, rubric, repository and inference lane still determine reuse. Current
policy is checked before work, external calls and publication. Disabling or
forgetting data does not become a cache bypass.

The following changes address the real-history recovery path:

- When no reusable task view exists, recovery compares the current request with
  admitted primary user goals and independently confirms a matching work anchor.
  Delegated requests cannot become primary anchors. A later assistant report
  after an interrupted turn can supply historical outcome context, while the
  current request's own subsequent output is excluded. Reports never establish
  user approval or current implementation success. If an existing task frame is
  too large for the optional identity comparison, its exact primary goal can be
  used for routing; the frame's full source constraints are retained for recovery.
- Local indexing commits groups of changed records instead of running Git policy
  checks and a transaction for each record. Every eligible record is retained.
- Recognized successful tool output can be represented by an invocation descriptor
  while Jev decides whether this question needs its body. User and assistant text,
  unknown formats, failed commands, explicit tool errors and established task
  evidence stay on the full-source path. Deferred bodies remain available by
  exact-source inspection and are explicitly unreviewed, not verified or absent.
- Source-wide classification no longer directly expands every potential change
  into a Cartesian product of historical assertions. A sentence-level change
  judgment and property-group lookup identify candidate pairs. Uncertainty expands
  to the existing exact pair comparison; source assertions are never deleted by
  these routing judgments. Supersession still requires an exact-source review.
- Dependency IDs remain in cache identity and local validation rather than being
  repeated in the provider input for each group lookup.
- Recovery progress is durable and written to stderr. stdout retains the JSON
  result contract. SIGINT records `RECOVERY_INTERRUPTED` and exit 130, releases
  an interrupted worker lease, and preserves already completed cache entries.
- Authentication failure stops further external calls for that operation,
  including later task-routing stages. A new operation can retry after credentials
  are corrected.
- Native `image_gen.generation` records retain their ID, prompt, status, failure
  and saved path without inline image bytes. Previously unsupported image records
  can be reinterpreted after checking their original hash, offset, session and
  current admission. The transaction preserves event ID and sequence, increments
  the revision, keeps original lineage, and invalidates old inventory-dependent
  packs. Forgetting or revoking admission prevents resurrection.
- General continuation with an established goal report delivers the goal, its
  reports and direct image observations first. Earlier reports in that session
  and other history have separate source-location labels. Same-turn primary
  request links identify historical scope when unambiguous; a missing link stays
  unresolved. Repeated labels such as A/B/C do not establish shared artifact
  identity, current approval or completion across source scopes.
- Supporting tool bodies remain in detail/audit with exact expansion commands.
  They may contain failures or qualifications and must be opened before making
  a claim that depends on their contents. Exact evidence questions retain the
  evidence view. The brief shares repeated record defaults and retains all
  nondefault assertion states and relation endpoints; the full projection stays
  available in detail/audit.

These are context packing and detail-selection decisions, not local daily-call,
request-byte or history-count quotas. Provider context errors still split requests
without dropping source text. Progress is an operation record; it does not certify
source coverage, successful delivery, applied context or current product behavior.

A runtime binding change invalidates in-flight work and old recovery packs as
before. It can reuse pure judgments after current authorization and dependency
checks. Exact user-reviewed legacy relationship IDs are preserved when the source
pair, model, rubric and inference lane match.

For a deferred output that is the only source of an artifact path, authorization,
error, qualification or observation boundary, read its exact source before relying
on that fact. A tool exit code, inventory, file path or receipt is not evidence of
visual quality, user approval, integration or current test success.

Validation used an isolated copy of 6,475 admitted real-history candidates and a
synthetic continuation request. It did not update the user's live store or plugin.
After cache population, one measured repeat created a pack in 13.28 seconds with
no HTTP calls. Its receipt completion interval from the stored pack timestamp was
16.71 seconds; this includes pack preparation and is not an additional latency
to add to dispatch time. The later
runtime-binding/epoch test reused all completed source and relationship judgments
and created its pack in 16.13 seconds. Eight previously oversized representation
requests were retried and still failed, so `quality: degraded` was retained.
The reordered brief was 945,763 bytes; the goal, current generated image paths,
proposal/selection distinction and existing-asset limitations were on page one.
These are warm, frozen-history measurements, not a cold-start or installed-runtime
claim. The earlier relation-building prototype took 1,971.72 seconds and produced
a 5.77 MB required brief; that prototype is not an acceptable cold-start result.

The cooperating source session accepted the reordered candidate for this frozen
continuation question after checking the first-page facts, historical request
bindings and an exact-source expansion from another working directory. It did not
attest every historical assertion, installed behavior, cold recovery or current
product verification. The final source passed 239 regression tests, the plugin
validator, source/bundle consistency and an isolated wheel import/CLI smoke check.
