# JCM — Jev Context Manager

A Codex plugin that carries working context across sessions. It automatically records conversations and tool results, then restores the context needed for the current request in a new session.

JCM 1.0.2 preserves the required state across seven prepared continuity flows.
In two matched consumer orders, all 28 answers and primary gates passed while
aggregate consumer input fell by 14.0807%. Uncached input increased slightly;
this does not establish monetary savings. See the [release notes](docs/releases/1.0.2.md)
for the measured scope and limits.

## Features

- **Choose scope or work** — A bare skill invocation previews a new conversation before you choose where recording begins; managed projects offer work to continue or a new task.
- **Automatic capture** — Saves public messages and supported tool results locally for registered sessions in enabled projects.
- **Existing-session adoption** — Imports the history of an ongoing session and continues capturing new records.
- **New-session recovery** — Retrieves earlier requirements and recent changes without a separate summary or handoff.
- **Reusable task context** — Reuses source classifications, task membership, and evidence representations across sessions; new corrections reassess their source dependencies.
- **Question-specific context** — Selects evidence and detail for the current question, separately from task identity. Equivalent questions can reuse selections while constraints and corrections remain preserved.
- **Delivery integrity** — Checks that the planner’s required exact spans, provenance and unresolved state survive rendering; repairs are degraded and unrecoverable gaps block delivery.
- **Layered recovery** — Restores the task frame and essential evidence first. Detailed sources and judgment audit data are available separately.
- **Retained-context changes** — An explicit current-context handle requests only changed records and fields within the same session. Fresh or compacted contexts receive the complete question-specific brief.
- **Evidence lookup** — Searches a pack by words, paths or Jev semantic matching, then expands exact sources without browsing the audit. Search snippets never count as source reads.
- **Observed costs** — Records provider calls, transmitted bytes, returned usage, cache reuse, and context delivery. Host model consumption is reported separately when observable.
- **Recovery progress** — Reports active phases and reusable work during recovery. Interrupted requests keep completed judgments and report cancellation explicitly.

## Installation

Requires local Codex and Python 3.11 or later.

```sh
codex plugin marketplace add oozoofrog/jev-context-manager --ref main
codex plugin add jev-context-manager@jcm
```

To use Jev, set `TYPESAFE_API_KEY` in the environment where Codex runs. JCM reads public transcript records by their structure, independently of the Codex version. Unknown record structures are reported as gaps; they do not prevent supported records from being captured.

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
