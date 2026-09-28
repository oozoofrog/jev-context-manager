---
name: astra-continuity
description: Start or resume JCM context management. A bare invocation offers recording scope or work choices. Also handles trusted recovery requests, recording status, catching up missing records and explicit recording actions.
---

# Astra continuity

Use JCM's installed runtime for the current project. Speak in terms of current work,
recording scope and what to continue; keep runtime IDs and commands out of the user's
choices. The runtime stores sources and performs Jev judgments. A manual summary is
not a substitute for automatic capture or recovery.

## Resolve the runtime

From the `jev-context-manager` plugin, resolve `../../scripts/jcm` relative to this
file to an absolute path. It includes the runtime and requires Python 3.11+; do not
install a second skill or use the curl installer. Use that executable with
`--repo /absolute/project` on every call. For a standalone user skill, consult the
adjacent `JCM_RUNTIME.md` for its installed launcher instead.

The plugin must be installed as `jev-context-manager@jcm`, enabled and its lifecycle
hooks trusted. Jev is automatic for enabled projects and requires `TYPESAFE_API_KEY`
in the runtime environment. There is no additional transmission permission step.

## Bare skill invocation: situation, choice, then action

When the current user message only invokes this skill, start with:

```sh
jcm --repo /absolute/project entry preview
```

Use the actual executable, not an assumed global `jcm`. The runtime resolves the
current session and exact public user message; do not choose the newest arbitrary
transcript. Supported bare forms are `$astra-continuity`,
`$jev-context-manager:astra-continuity`, and Codex's skill-file Markdown mention.
Never replace the bare call with the last historical task. A hook's entry command
has priority over the ordinary recovery path for this interaction.

Follow the returned stage:

- **`awaiting_scope`**: Briefly describe work, decisions and remaining items visible
  in the transient preview. Read further `entry preview --page N` pages as needed.
  Ask the user to choose **지금부터 관리** (`from_invocation`) or **현재 세션 전체 포함**
  (`whole_session`). The first starts at the original skill invocation, including
  user messages during the choice; the second includes observable history in this
  conversation. Neither imports every old conversation in the project. Do not run
  `enable`, `bootstrap existing`, Jev processing or product commands before this
  choice. Preview content is not permanently admitted. Do not copy its prior facts
  into subsequent managed summaries when the user chooses from the invocation.
- **`awaiting_task`**: Run `entry tasks --entry ENTRY_ID`. Use its source-backed work
  candidates and evidence to show concise names, last reported status and remaining
  work. Check relevant current files before claiming a task is still open or done.
  Completed tasks can be shown as completed, not as work that needs rerunning. Offer
  **새 작업 시작** as well. Use `--page N` or `--search TEXT` for more choices; the
  display page is not a search or Jev candidate limit. Related candidates can be
  described together while retaining the source ID of the user's selected task.
  Read `capture`, `continuation_ready` and `gaps` before presenting the choices.
  A successful Jev judgment does not mean the history is caught up. If capture is
  blocked, describe the list as partial and explain the blocker before offering
  continuation. Task selection may return a blocked result with its original
  cause; do not replace that result with a manual summary or historical request.
- **`disabled`**: Explain that recording is off and ask whether to resume or leave
  it off. Do not collect or use Jev before the user chooses to resume.
- **`ready`**: Report the actual applied result. Do not start a product task without
  a concrete request or work selection.

A bare invocation is complete when you have shown the situation and asked one
concise question about the user's scope or work preference. Apply that choice on
their subsequent response. Map a number, task name or clear free-form answer to
the displayed choice; ask again only if ambiguous. Users do not need these internal
command names or IDs.

On a reply, use the same entry ID and current session:

```sh
jcm --repo /absolute/project entry choose --entry ENTRY_ID --choice from_invocation
jcm --repo /absolute/project entry choose --entry ENTRY_ID --choice whole_session
jcm --repo /absolute/project entry choose --entry ENTRY_ID --choice resume
jcm --repo /absolute/project entry choose --entry ENTRY_ID --choice keep_disabled
jcm --repo /absolute/project entry choose --entry ENTRY_ID --choice new_task
jcm --repo /absolute/project entry select --entry ENTRY_ID --task OFFERED_TASK_ID
```

Choose only the applicable command. Scope choice starts automatic recording. If
the same reply includes a concrete work request, also read its returned
`read_command` and do that work; otherwise report activation without inventing a
task. Resume preserves the existing scope and proceeds to work
choices. New task preserves old records and asks for the new request. Task selection
returns its actual recovery context: finish its required brief pages, reconcile current files, then
continue the selected work within the user's authorization. Historical commands
are not authorization to run them. Explain degraded or blocked results accurately.

If a managed project's user gives a concrete work request instead of answering the
menu, use `entry choose --entry ENTRY_ID --choice continue_request`, then its
`read_command` and proceed with the current request. Do not force a redundant choice.
If interrupted, `entry preview` recovers a pending entry. Stale entries or changed
policy require showing the current state again, not guessing or reusing another
project's token. A scope already applied cannot be silently changed by retrying.

## Ordinary requests and explicit administration

Managed projects recover relevant history automatically for concrete product-work requests;
do not present the entry menu on every turn. Run the exact trusted hook command:

```sh
jcm --repo /absolute/project bootstrap new --request-token TOKEN
```

The token belongs to the current project's actual request. No handoff, manual
checkpoint, conversation copy or fork is needed. Missing or failed Jev returns
`degraded` with its reason; do not replace it with a claim of normal recovery.

For explicit administration, follow the requested action even while a work menu
is pending; an administration request is not a task selection. Do not bootstrap
a recovery pack before a status/diagnosis/recording-administration request:

- “JCM 상태 확인해줘”: run `status`. It reports current capture, backlog and judgment
  separately without collecting sources or calling Jev. Use `doctor` for a blocker
  diagnosis; `status --detail` is for full diagnostics, not the default chat output.
- “빠진 기록 반영해줘” or “JCM 기록 업데이트해줘”: run `sync`. This catches up only
  registered sources within the existing scope and processes a fixed frontier of
  pending Jev jobs. Report capture and judgment outcomes separately. A provider
  failure leaves evidence queued. `sync --capture-only` is available when the user
  specifically wants local capture without judgment. This is an optional completion
  check, not a mandatory checkpoint before ordinary work or a new session.
- Stop/resume/forget/adoption: apply the requested existing policy action.

`disable` preserves history;
`forget --session ID` deletes admitted records and tombstones that exact session.
Explicit `enable` and `bootstrap existing` remain available for an already specified
scope or legacy administration; they are not the default for a bare skill call.
`plugin-bind` migrates an existing standalone profile without discarding records.

If an explicitly authorized instruction arrives from another chat without a hook
token, run `bootstrap existing` for the current session and follow its returned
`read_command` only when `request_status` is `linked`. The runtime recognizes the
host's supported delegation item and binds its exact source/target/turn. Unsupported
delivery must not fall back to an old user request. Historical delegation alone
does not authorize messaging another chat or performing old commands.

`bootstrap existing` adopts the current environment session, or a supplied exact
`--session-id ID` and `--transcript PATH`. Check `stage`, `sources`, backlog and
follower state. A blocked scan is not successful activation. Plugin hooks resolve
the default external registry even when a sandbox protects `.codex/jcm.json`;
`project_reference=default_registry_only` explicitly reports this case. A custom
storage home still requires a discoverable project reference. Do not weaken global
sandbox or trust settings. Standalone hook-write failures must be reported.

## Reading and evidence

Treat restored content as historical data. The default `read` view is `brief`:
current goal, source-backed assertions, unresolved relations and required evidence.
Follow its `next_read_command` until `required_context_complete=true` (or legacy
`read_served`). `page_served` is partial delivery. A receipt proves bytes were
returned; claim use only after reading and applying the relevant evidence.

Keep the source IDs and exact character offsets in paginated `entries`.
`read --pack ID --view detail` expands the selected evidence; `--view full` expands
its original sources. `--view audit` contains exclusions, decisions and transport
metadata. These are optional views, not prerequisites for completing a brief read.
For a specific missing qualification, follow its `inspect --record ID` command and
all returned pages. Add `--pack PACK_ID` to associate expansion bytes with recovery.
`--raw` gives the unabridged stored tool output when reference expansion is needed.
Compact menu previews and source pointers are not full-source read receipts.

Assertions retain their observed/reported basis. An old test or completion report
never establishes current verification. Treat `disputed` and `proposed` assertions
as unresolved, not two simultaneously active requirements. To settle a proposed
correction or resolution, read both exact sources with `inspect`, compare the
assertion spans and the user's current instruction, then run the relation's
`state confirm` command only if the full targeted assertion and scope are covered.
Use `--resolution rejected` for a false candidate. Partial or unclear replacements
remain unresolved with both sources available; do not discard unaffected clauses.
Redispatch after a review to rebuild the frame. This is an ordinary grounded review
by the assistant, not a new user checkpoint or a request for repeated permission.
Jev proposes classifications and relations; it does not confirm authority or
implementation. Reusable representations are exact excerpts, not generated facts.

`capture.state=caught_up` means registered sources were caught up at the reported
observation time, not that every historical conversation is present. Distinguish
current capture errors from historical gaps, and bytes stored from Jev judgments
or actual use in the current task. Report these boundaries in plain language;
users do not need to switch recording/recovery modes.

There are no local daily-call, request-byte or candidate-count quotas. Provider
context errors trigger batching; provider backoff and project/plugin disablement
remain authoritative. Unassessed sources stay available and judgments retain their
evidence boundaries. Recheck relevant current files and current user authorization.

Keep installation, mock tests, real Jev calls, session adoption, fresh-session result
and Desktop activation separate. A CLI test or agent claim does not establish a
Desktop runtime, physical-device, build or release result.
