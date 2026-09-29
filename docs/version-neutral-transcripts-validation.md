# Codex-version-independent transcript capture — 2026-09-29

JCM now determines transcript compatibility from record structure and session/project
identity. `session_meta.cli_version` is optional provenance, never an admission gate.
There is no supported-Codex-version list in the runtime or discovery path.

## Behavior

- Older, current, future, and absent host-version metadata follow the same capture path.
  Paginated history from different Codex versions is retained in source order.
- Session identity, absolute project identity, admitted roots, symlinks, scope,
  tombstones, and private/instruction-record exclusions still apply.
- Unknown or malformed public structures retain a content-free source reference
  (path, offset, SHA-256) and a coverage gap. Their body is not guessed, captured as
  a user instruction, or queued for Jev. Supported records after them remain readable.
- An unresolved record, malformed tail, or partial tail prevents selection of an
  older user request. A later understood user message establishes a new current
  request without clearing historical coverage gaps.
- File changes, collaboration tool results, and web-search extension results are
  understood public tool observations. Sub-agent activity is a lifecycle record.
  They do not turn into user-request barriers.
- `codex-public-items-v3` identifies JCM's parser revision, independently of the
  Codex build. Upgrading from the previous parser creates a cursor that replays
  formerly skipped history. Existing event identities prevent duplicate logical
  events; new source references can revise events and queue their reassessment.

## Verification

| Check | Result |
|---|---|
| Full `unittest discover -s tests` regression suite | 199 passed |
| Compatibility tests from an installed wheel in a fresh virtual environment | 6 passed |
| Repository plugin payload versus canonical runtime | Exact match |
| Plugin manifest validation | Passed |
| Wheel runtime versus canonical runtime | All 33 Python files matched |
| `git diff --check` | Passed |

The compatibility regression covers old/future/missing version metadata, stable
request identity, malformed metadata and public payloads, unknown-record source
references, prevention of stale-request fallback, incomplete tails, and supported
tool observations. Existing tests cover mixed-version pagination and parser-cursor
upgrades alongside capture scope, privacy, concurrency, and recovery behavior.

An original 128,517,551-byte transcript was read with the repository plugin runtime.
Its unmodified metadata remains `0.155.0-alpha.9.2`. Metadata admission passed and
the current user request was resolved to the expected turn. The original file's
SHA-256 was unchanged. Session and turn identifiers remain in local evidence.

This read-only check produced 228 user/delegated-request records, 427 assistant
records, 2,199 tool records, and 10 lifecycle records. It also retained 133
unresolved request boundaries: 132 unknown raw records (voice/realtime and
inter-agent metadata) and one previously unsupported delegated-request envelope.
These occur before the current understood request. Unknown record coverage remains
explicit; this is not a claim that every source format is supported.

## Evidence and limits

Full local logs and the original-transcript result are in
`.task-notes/version-neutral/`: `red.log`, `tests-final.log`, `wheel-tests.log`,
`wheel-verification.json`, and `original-session-final.json`.

The initial wheel build without isolation could not import `setuptools.build_meta`
from the existing development environment. The normal isolated build succeeded;
the resulting wheel was then installed and tested in a fresh environment. The full
test run also emitted a non-failing SQLite `ResourceWarning`; its log is preserved.

These pre-release checks validated source and the repository's bundled plugin.
They did not update the user's installed plugin or ingest the transcript into the
live JCM store. Release and installation are verified separately by `scripts/release.py`.
The original-transcript check made no provider calls. Jev judgment, live-store
adoption, and restored work in another session are separate validations.
