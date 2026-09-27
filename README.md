# JCM — Jev Context Manager

A Codex plugin that carries working context across sessions. It automatically records conversations and tool results, then restores the context needed for the current request in a new session.

## Features

- **Automatic capture** — Saves public messages and supported tool results locally for registered sessions in enabled projects.
- **Existing-session adoption** — Imports the history of an ongoing session and continues capturing new records.
- **New-session recovery** — Retrieves earlier requirements and recent changes without a separate summary or handoff.
- **Jev-based selection** — Classifies records and evaluates their relevance to the current request to assemble the context to restore.

## Installation

Requires local Codex and Python 3.11 or later.

```sh
codex plugin marketplace add oozoofrog/jev-context-manager --ref main
codex plugin add jev-context-manager@jcm
```

To use Jev, set `TYPESAFE_API_KEY` in the environment where Codex runs. Transcript parsing currently supports Codex CLI `0.158.0-alpha.2.1`.

## Usage

See the [user guide](docs/user-guide.md) for setup, session recovery, record management, and troubleshooting.

In a new chat, review and trust the plugin's hooks, then make this request in the project you want to use:

```text
$jev-context-manager:astra-continuity
Enable JCM for this project and adopt the current session.
```

After that, request work as usual in a new session within the same project. JCM restores the previous context, and Codex checks the current files before continuing.

JCM uses Jev automatically for enabled projects, including records captured by older versions. There is no separate transmission permission step. There are no local daily-call, request-byte, or candidate-count quotas. If the API key is missing or the provider cannot produce a usable judgment, JCM restores available local records and marks the result as `degraded` with the actual reason.

## Record management

Records are stored in `~/Library/Application Support/JCM` by default. Use the skill to request these actions:

| Action | Command |
|---|---|
| Check capture and recovery status | `status` |
| Stop capture for a project | `disable` |
| Delete a session and prevent recapture | `forget --session ID` |

Stopping capture or removing the plugin preserves stored records. Re-enabling it may also recover previously uncaptured records from registered sessions.

## Updating and removing

Open a new chat after updating.

```sh
codex plugin marketplace upgrade jcm
codex plugin add jev-context-manager@jcm
```

```sh
codex plugin remove jev-context-manager@jcm
```
