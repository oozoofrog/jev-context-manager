# Compatibility evidence — 2026-09-27

## Starting state

- Workspace: `/Volumes/eyedisk/develop/oozoofrog/jev-context-manager`.
- Initially empty; `git status` returned “not a git repository”. No Git repository
  was created. No owning/ancestor `AGENTS.md` file was present; the user's supplied
  working instructions apply.
- Installed executable: `/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`.
- Version: `codex-cli 0.158.0-alpha.2.1`.
- Configured user model/reasoning at inspection: `gpt-6-astra` / `xhigh`.
- `TYPESAFE_API_KEY` was present; only its presence was printed, never its value.
- Existing user `hooks.json` contained an empty hook map. Existing user settings
  and unrelated plugin/hook trust entries were not repurposed by JCM.

## Source contracts

The [official hook guide](https://learn.chatgpt.com/docs/hooks) was fetched and
saved in `evidence/codex-hooks.md`. It documents project hook discovery, hash-based
trust, stdin JSON, fixed additional context and the one-off automation trust
bypass. Hosted and specialized tool paths are not complete capture boundaries.
The transcript file is explicitly an unstable interface.

The [TypeSafe API](https://docs.typesafe.ai/api),
[Choice](https://docs.typesafe.ai/primitives/choice),
[Score](https://docs.typesafe.ai/primitives/score),
[reranking cookbook](https://docs.typesafe.ai/cookbooks/rerank_typesafe) and
[model catalog](https://docs.typesafe.ai/models) were fetched live and saved under
`evidence/typesafe-*.md`. The web renderer failed on some `.md` URLs; direct HTTPS
fetch succeeded. This was not treated as a service outage.

The implemented wire contract is `POST https://api.typesafe.ai/v1/systemone`
with `state`, `model`, `questions`; the response has `model`, `answers`, `usage`.
The pilot pins `jev-1.13.0`. Noul is a yes probability, Choice uses explicit options,
and Score uses ordered descriptive levels. There is no generated summary or
free-form explanation attributed to Jev. Dollar cost is reported as unknown;
request and token usage are retained. No general retention or ZDR guarantee is
inferred from API success.

## Actual host probe

| Surface | Observed | Boundary |
|---|---|---|
| `SessionStart` | Actual CLI startup payload | Fixed bootstrap supported |
| `UserPromptSubmit` | Prompt, turn ID, session ID and cwd | Captured before model processing |
| `PostToolUse` | Bash input, result and tool-use ID | Some hook outputs omit explicit exit status |
| `Stop` | Latest public assistant message | Not relied upon to preserve a preceding user correction |
| `SessionEnd` | Normal CLI termination payload | Not relied upon; hard termination can skip it |
| `PreCompact`, `PostCompact`, `Interrupt` | Configured from public contract | Actual runtime event not yet exercised |
| Transcript | Versioned JSONL, session metadata, public completed items | Only this exact probed version is admitted |
| Public user messages | `event_msg.item_completed.UserMessage` | Wrapper instructions/response duplicates excluded |
| Public assistant messages | `AgentMessage` phase `commentary` / `final_answer` | Private reasoning is never ingested |
| Command results | `CommandExecution`, stable execution ID | Hook and transcript attach source refs to one observation |
| Other public items | Explicit unsupported gap | No invented parsing of new shapes |
| Desktop current chat | CLI install source shared with app | Desktop lifecycle/UI reload remains unverified |

The first and third probes used `--ignore-user-config` and did not receive project
hooks, even when the canonical cwd trust override was present. The second probe
loaded normal config with canonical project trust and received all five listed
runtime events. This is an installed-host observation, not a universal assertion
about every Codex version.

Codex exec also persisted a `projects.<temporary fixture>.trust_level` entry in
the user settings. The harness now backs up settings, identifies only its newly
created fixture entry, removes that exact entry, and compares the remaining parsed
configuration. It does not overwrite unrelated concurrent changes. The earlier
probe/failed-pilot entries were separately backed up and removed; see
`evidence/host-config-cleanup.json`.

The first real fresh-session pilot completed the continuity task locally but its
Jev transport failed inside the agent tool environment. That pilot is retained as
a degraded result. The next pilot explicitly enables network access for the
test session's workspace sandbox; it does not change global sandbox settings.

## Capability gate

Installation, project trust, hook-definition trust, event observation, parser
coverage, provider calls, pack creation, read serving and successful continuation
are independent checks. A one-off test trust bypass is not production hook trust.
The runtime stays conservatively `limited`; individual successful bounded Jev
dispatches can report `quality=normal` while coverage remains `partial`.
