# JCM User Guide

JCM records a project's session history and restores relevant context when you continue work in a new Codex session.

## Install and prepare

Requires local Codex and Python 3.11 or later. Transcript parsing currently supports Codex CLI `0.158.0-alpha.2.1`.

```sh
codex plugin marketplace add oozoofrog/jev-context-manager --ref main
codex plugin add jev-context-manager@jcm
```

Open a new chat and review and trust the plugin's hooks. To use Jev, provide `TYPESAFE_API_KEY` in the environment where Codex and its commands run. Setting it in a terminal does not change the environment of an already running desktop app.

Installing the plugin does not activate projects. Enable each project you want JCM to record.

## Start recording a project

In a chat for that project, send:

```text
$jev-context-manager:astra-continuity
Enable JCM for this project and adopt the current session.
I authorize sending context to Jev.
```

JCM registers the project, imports the current session's supported public history, and starts capturing new records. Adoption also works for a session that began before JCM was installed.

For local-only recording, replace the last line with `Keep Jev transmission disabled.` You can allow transmission later, but records captured while it was disabled remain ineligible for transmission.

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
| `event_count` | Number of stored events |
| `hook_events_received` | Hook events received by this project |
| `followers[].running` | Whether an adopted session's local follower is running |
| `plugin.active` | Whether the persisted plugin state and installed bundle pass JCM's checks; hook trust is separate |
| `allow_egress` | Whether eligible records may be sent to Jev |
| `bootstraps[].stage` | For existing-session adoption: `captured` or `blocked`; a blocked scan has not activated capture |
| `bootstraps[].sources` | At adoption time: validated segments with durable cursors and remaining bytes |
| `gaps` | Missing or unsupported source coverage |

For a restored context pack, `quality=normal` means the normal Jev path succeeded. `degraded` means JCM used a fallback; inspect the reported reason. `stage=blocked` means the pack was not delivered. The project status value `mode=limited` alone does not mean recording has stopped.

## Stop, resume, or delete records

Ask the skill to perform the action for the current project:

```text
$jev-context-manager:astra-continuity
Stop JCM capture for this project. Keep the stored history.
```

To resume, ask it to re-enable the project without changing the Jev policy and adopt the current session again. To stop Jev calls while keeping local capture, ask it to deny Jev transmission instead.

Stopping capture preserves stored history. Re-enabling can recover records written to registered transcripts while capture was stopped. To exclude a particular session permanently, identify its exact session ID and ask the skill to forget it. This deletes its admitted records and prevents JCM from collecting that session again.

Records are stored in `~/Library/Application Support/JCM` by default. Removing the plugin also preserves this history.

## Direct commands

The plugin includes its own executable; it does not require a global `jcm` command. Ask the skill for the absolute path to its bundled `scripts/jcm`, then set these variables to your actual paths:

```sh
JCM="/absolute/path/to/installed/plugin/scripts/jcm"
PROJECT="/absolute/path/to/project"
```

For a new registration with Jev transmission allowed:

```sh
"$JCM" --repo "$PROJECT" enable --allow-jev-egress
"$JCM" --repo "$PROJECT" bootstrap existing
```

Run `bootstrap existing` inside the Codex session being adopted. From an external terminal, provide the exact session explicitly:

```sh
"$JCM" --repo "$PROJECT" bootstrap existing --session-id SESSION_ID
```

For an existing registration, use `policy` to change transmission settings; rerunning `enable` with a different policy is rejected.

| Action | Command |
|---|---|
| Check status | `"$JCM" --repo "$PROJECT" status` |
| Allow Jev transmission for newly captured records | `"$JCM" --repo "$PROJECT" policy --egress allow` |
| Deny Jev transmission, keep local capture | `"$JCM" --repo "$PROJECT" policy --egress deny` |
| Stop capture | `"$JCM" --repo "$PROJECT" disable` |
| Resume without changing transmission policy | `"$JCM" --repo "$PROJECT" policy --enabled true` |
| Delete a session and block recapture | `"$JCM" --repo "$PROJECT" forget --session SESSION_ID` |

If the project was registered with the standalone CLI, migrate it using the plugin executable, then adopt the current session:

```sh
"$JCM" --repo "$PROJECT" plugin-bind
"$JCM" --repo "$PROJECT" bootstrap existing
```

Migration preserves records and transmission policy, stops the old follower, and backs up and removes that registration's old project hook commands.

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
| Jev result is `degraded` | Check `allow_egress`, key availability, network access, and the reported provider error or call-budget limit. Older records captured with transmission denied remain denied. |
| Recovery is `blocked` | Inspect and resolve the reported coverage or size limit before retrying. A blocked pack has not been delivered. |
