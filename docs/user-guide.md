# JCM User Guide

JCM records a project's session history and restores relevant context when you continue work in a new Codex session.

## Install and prepare

Requires local Codex and Python 3.11 or later. Transcript parsing currently supports Codex CLI `0.158.0-alpha.2.1`.

```sh
codex plugin marketplace add oozoofrog/jev-context-manager --ref main
codex plugin add jev-context-manager@jcm
```

Open a new chat and review and trust the plugin's hooks. To use Jev, provide `TYPESAFE_API_KEY` in the environment where Codex and its commands run. Setting it in a terminal does not change the environment of an already running desktop app.

Installing the plugin does not activate projects. Invoke the skill in the project you want to manage.

## Start or choose work

Send only the skill mention:

```text
$jev-context-manager:astra-continuity
```

For a new project, JCM first previews the current conversation and briefly describes its work, decisions and remaining items. It then offers:

- **From this invocation**: record this skill call and subsequent user messages and work. Earlier conversation content and the preview derived from it are excluded from managed history.
- **Whole current session**: also include the observable prior history of this conversation. Other old conversations are not automatically imported.

The boundary is the original skill invocation, not the time you answer. No project is enabled and no preview is sent to Jev before you choose. Pending choices survive interruption. Answer with a number or ordinary text; you do not need JCM commands.

For an already managed project, JCM shows source-backed work choices and **Start a new task**. It checks the current files before claiming old work is still open or complete. Selecting work recovers the relevant decisions, corrections and common constraints; it does not authorize unrelated historical commands. More choices remain accessible through more pages or search.

For a disabled project, JCM asks whether to resume. Resuming preserves its recording scope and stored history, then shows work choices. Existing profiles retain their prior scope when updated.

Jev is used automatically once a project is enabled. Provide `TYPESAFE_API_KEY` in the environment where Codex runs; no separate transmission permission is required. If Jev cannot produce a usable judgment, recovery is marked `degraded` with the actual reason.

## Continue in a new session

Open a new chat in the same project and request the next piece of work normally:

```text
Continue the reconnect work. Check the current implementation and preserve the earlier requirements.
```

JCM retrieves registered history and uses Jev to select context for this request. Codex reads that context and checks the current files before continuing. No manual summary, handoff, or repeated adoption is needed for each new session.

If the existing session's local follower has stopped after a long idle period, ask the skill to adopt that same session again. Registered history can still be recovered on the next request even when the follower is not running.

## Check recording and recovery

```text
$jev-context-manager:astra-continuity
Check JCM status for this project. Report whether capture is active,
whether the latest request used Jev, and any gaps in recovered history.
```

Useful fields in the response:

| Field | Meaning |
|---|---|
| `capture_scope` | Chosen initial session and invocation boundary, or `legacy_project` for existing profiles |
| `project_reference` | `default_registry_only` means the plugin uses its default external registry because the project directory is protected |
| `event_count` | Number of stored events |
| `hook_events_received` | Hook events received by this project |
| `followers[].running` | Whether an adopted session's local follower is running |
| `plugin.active` | Whether the persisted plugin state and installed bundle pass JCM's checks; hook trust is separate |
| `allow_egress` | Always `true`; retained for compatibility with older status readers |
| `bootstraps[].stage` | For existing-session adoption: `captured` or `blocked`; a blocked scan has not activated capture |
| `bootstraps[].sources` | At adoption time: validated segments with durable cursors and remaining bytes |
| `gaps` | Missing or unsupported source coverage |

For a restored context pack, `quality=normal` means the normal Jev path succeeded. `degraded` means JCM used a fallback; inspect the reported reason. `stage=blocked` means the pack was not delivered. The project status value `mode=limited` alone does not mean recording has stopped.

## Large histories and paged recovery

Jev retrieval uses soft estimates for the documented 32k state-plus-longest-question and 64k total token contexts. The estimate is not a tokenizer or an admission limit. An explicit server context rejection triggers smaller batches or source spans; an estimate alone never rejects a request. A large
record is split at paragraph or output-line boundaries where possible; each span
retains its source ID, revision and character offsets. Adjacent user requirements
are compared in a separate pass so corrections can cross retrieval batches. A
requirement pair that cannot fit together is reported as unresolved rather than
silently compared from truncated text.

Successful judgments are cached. Retrying the same retrieval reuses successful
batches and retries failed ones. Confirmed context rejections also reuse their split paths. There are no local daily-call, byte-size or candidate-count quotas, including for existing profiles. An
unassessed record remains available locally. Old per-record denial flags are
ignored; records do not need to be imported again to use Jev.

Large recovery packs return `stage=reading`, `delivery=page_served` and a
`next_read_command`. Read each page and follow that exact command until it is null.
You can explicitly reread a page with:

```sh
"$JCM" --repo "$PROJECT" read --pack PACK_ID --page 2
```

Each page contains `entries` with paths into the immutable pack. Oversized text
fields carry character offsets; records keep their source IDs. Snapshot metadata
is paged too. No original text is silently truncated. Page hashes and read receipts
track delivery; repeating a page does not count as reading a different one.
`pagination.remaining_pages` reports pages not yet served, and `read_served` is
reached only after every page has been served. These receipts do not prove that an
agent consumed the content or resumed work correctly. Interrupted reading remains
partial and can continue without generating a new pack.

## Stop, resume, or delete records

Ask the skill to perform the action for the current project:

```text
$jev-context-manager:astra-continuity
Stop JCM capture for this project. Keep the stored history.
```

To resume, invoke the skill and choose to resume recording. The existing scope is preserved.
Disabling JCM stops both collection and Jev use for that project.

Stopping capture preserves stored history. Re-enabling can recover records written to registered transcripts while capture was stopped. To exclude a particular session permanently, identify its exact session ID and ask the skill to forget it. This deletes its admitted records and prevents JCM from collecting that session again.

Records are stored in `~/Library/Application Support/JCM` by default. Removing the plugin also preserves this history.

## Direct commands

The plugin includes its own executable; it does not require a global `jcm` command. Ask the skill for the absolute path to its bundled `scripts/jcm`, then set these variables to your actual paths:

```sh
JCM="/absolute/path/to/installed/plugin/scripts/jcm"
PROJECT="/absolute/path/to/project"
```

For explicit legacy project-wide registration (bypasses the interactive scope choice):

```sh
"$JCM" --repo "$PROJECT" enable
"$JCM" --repo "$PROJECT" bootstrap existing
```

Run `bootstrap existing` inside the Codex session being adopted. From an external terminal, provide the exact session explicitly:

```sh
"$JCM" --repo "$PROJECT" bootstrap existing --session-id SESSION_ID
```

Jev transmission is automatic. The old `--allow-jev-egress` and `policy --egress`
options have been removed.

| Action | Command |
|---|---|
| Check status | `"$JCM" --repo "$PROJECT" status` |
| Stop capture | `"$JCM" --repo "$PROJECT" disable` |
| Resume JCM | `"$JCM" --repo "$PROJECT" policy --enabled true` |
| Delete a session and block recapture | `"$JCM" --repo "$PROJECT" forget --session SESSION_ID` |

If the project was registered with the standalone CLI, migrate it using the plugin executable, then adopt the current session:

```sh
"$JCM" --repo "$PROJECT" plugin-bind
"$JCM" --repo "$PROJECT" bootstrap existing
```

Migration preserves records, stops the old follower, and backs up and removes that registration's old project hook commands. Jev is automatic after migration.

## Update or remove

```sh
codex plugin marketplace upgrade jcm
codex plugin add jev-context-manager@jcm
```

Open a new chat after updating. If you use direct commands, resolve the installed executable path again because an update may change it.

```sh
codex plugin remove jev-context-manager@jcm
```

Disabling the plugin in Codex stops subsequent guarded capture and its follower. To stop a single project's collection explicitly, use `disable`; changing hook trust alone is not a stop command for an already running follower.

## Troubleshooting

| Symptom or error | Action |
|---|---|
| Skill is missing | Check that `jev-context-manager@jcm` is installed and enabled, then open a new chat. |
| `PROJECT_NOT_ENABLED` | Enable JCM for the intended project root. Installation alone does not register it. |
| `PLUGIN_INACTIVE_OR_UNVERIFIED` | Check plugin enablement, the installed bundle, and project hook settings. After an update, use the current bundled executable. |
| No new events | Check the project root, project enablement, and hook trust. For an already running session, adopt it and check the follower. |
| `CURRENT_SESSION_ID_MISSING_OR_AMBIGUOUS` | Run adoption inside the intended Codex session, or specify its exact `--session-id`. |
| `TRANSCRIPT_NOT_FOUND` | Confirm the session ID and transcript location. If needed, add `--transcript /absolute/path/to/session.jsonl`; it must be inside the admitted transcript roots. |
| `TRANSCRIPT_LINE_TOO_LARGE` | A single JSONL line exceeds the 8 MB read bound. The cursor stays before that line; total transcript size is not capped at 32 MB. |
| `PAGINATED_HISTORY_COVERAGE_PARTIAL` | Supported continuation segments were found; this does not prove that the entire earlier history was imported. |
| `UNSUPPORTED_TRANSCRIPT_VERSION` | The session's transcript format is not supported. Do not edit its version metadata to force ingestion. |
| Jev result is `degraded` | Check key availability, network access, and the reported provider error. Jev needs no separate permission; old denial flags do not block it. |
| Recovery is `reading` | Read the returned page and follow `next_read_command`; the pack spans multiple bounded responses. |
| Recovery is `blocked` | Inspect the reported reason. Even the minimum page envelope may not fit an unusually small delivery limit. A blocked pack has not been delivered. |

## Provider failures and high-volume usage

JCM records every attempt and returned token usage without imposing a daily request
count or monetary budget. All eligible historical candidates are assessed; page
sizes govern lossless delivery, not how much history can be recovered.

HTTP 429/5xx and transport failures are retried up to three attempts per invocation.
This failure-recovery bound is not a workload quota: subsequent invocations can
retry failed work and reuse successes. The provider's `Retry-After` (seconds/date)
or `retry-after-ms` is honored without shortening it. During long waits, project
policy is rechecked and worker leases are renewed. Persistent failure returns an
explicit degraded result instead of waiting forever.

Private `provider_errors` rows retain status, provider request ID and a redacted
error body and retry headers (up to 64 KiB of diagnostic body data). They are removed with derived data
on `forget`. Generic HTTP 400 errors do not trigger arbitrary splitting; confirmed
context failures do, including Jev's observed `detail.error_type=max_tokens_exceeded`.
An oversized indivisible query or full relationship pair remains unresolved with
its original local source available.

The journal schema advances from v1 to v2 without dropping records. Older runtimes
reject v2 journals rather than running deletion code unaware of the new diagnostic
data. Back up the external store before an installed-runtime upgrade; rollback of
the executable alone does not downgrade the journal.
