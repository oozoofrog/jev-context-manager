# Jev Context Manager — Astra Continuity

Python CLI/runtime for the supplied [Astra Continuity v1.0 design](docs/JCM_Astra_Continuity_Design_v1.0.md).
This is the first automatic continuity slice, **not a v1 production release**.
JCM supports both Git repositories and non-Git workspaces. It does not initialize
Git as part of continuity setup or operation.

## Implemented path

```text
Codex UserPromptSubmit / PostToolUse / Stop / SessionStart
  → scope check + redaction
  → atomic source blob + SQLite event/cursor/queue commit
  → fixed bootstrap + opaque request token
  → new session dispatch
  → recover admitted source tails + pending records
  → bounded Jev classifications / relationships / relevance / representation
  → reconcile current files and Git state
  → immutable context pack
  → explicit pack read + current-file read + continuation
```

No checkpoint or handoff command is required. Jev is part of normal dispatch;
network failure, denied egress and unavailable credentials produce explicit
`degraded` quality. Retrieval still protects user sources and pending tail records.
An installed hook, a created pack, a served read, and a correct new-session result
are separate observations.

See [requirement → acceptance → module mapping](docs/acceptance-map.md) and
[host/API compatibility](docs/compatibility.md). Validation results and their
limits are in [the initial validation report](docs/validation.md) and
[the bootstrap validation report](docs/bootstrap-validation.md).

## Install directly from GitHub into local Codex

Run this in a local terminal on macOS or Linux; no checkout or `sudo` is needed:

```sh
bash -o pipefail -c 'curl -fsSL https://raw.githubusercontent.com/oozoofrog/jev-context-manager/main/install.sh | bash'
```

Requires **Python 3.11+**, Python's `venv`/pip support, `curl`, and HTTPS access to
GitHub and PyPI for build dependencies. A local Codex app/CLI is needed to use the
installed skill. On Linux distributions that split out `python3-venv`, install that
prerequisite using your system package manager first. Runtime itself has no third-party
Python dependencies. `JCM_PYTHON` selects a different Python executable.

| Installed item | Default location |
|---|---|
| Versioned CLI virtual environments and source provenance | `~/.local/share/jcm/releases/` |
| Stable runtime selector | `~/.local/share/jcm/current` |
| CLI launcher | `~/.local/bin/jcm` |
| Local Codex skill | `~/.agents/skills/astra-continuity/` |
| Previous files and rollback index | `~/.local/share/jcm/backups/` |

The skill is installed in Codex's documented
[user skill directory](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills).
It includes the absolute CLI path, so Codex does not depend on a GUI shell's PATH.
If it does not appear in the skill selector, refresh/restart Codex. Installing the
files does not establish that the current app session has loaded the skill.

The installer leaves Codex global configuration, API credentials and shell startup
files untouched. Add `~/.local/bin` to PATH yourself if desired, or use the absolute
launcher below. It does not activate arbitrary projects, ingest current conversations
or make Jev calls during installation.

### Connect a project after installation

For local capture with Jev egress denied:

```sh
"$HOME/.local/bin/jcm" --repo /absolute/project enable --install-hooks
```

For a **new registration** that you explicitly authorize to send admitted context
to Jev, use this command instead, with `TYPESAFE_API_KEY` already supplied through
your environment:

```sh
"$HOME/.local/bin/jcm" --repo /absolute/project enable --install-hooks --allow-jev-egress
```

The key is not written by the installer. Existing registrations keep their policy;
use `policy --egress allow` for an explicit policy change. Previously denied records
remain denied. Review the installed hook definitions in Codex's `/hooks` surface;
the installer does not bypass project or hook trust.

In a session already running before installation, invoke `$astra-continuity` and
ask it to bootstrap the current session, or run this **inside that Codex session**:

```sh
"$HOME/.local/bin/jcm" --repo /absolute/project bootstrap existing
```

This resolves the real current session identity, backfills public history and starts
the bounded follower. From an outside terminal, identify the session explicitly with
`--session-id ID` (and optionally `--transcript PATH`). Fresh sessions use the
SessionStart/request hook path automatically after project activation and trust.
Historical records remain in the external private `JCM_HOME` described below;
installation does not create a project `.jcm/` directory.

### Update, pin or customize

Run the same installation command again to update. It resolves the selected ref to
an immutable commit, builds and checks a new environment before activation, and
retains the previous release and replaced files. Ordinary activation failures roll
back the launcher, runtime selector, skill and install manifest. This is not a
power-loss transaction guarantee. Download/build failures leave the active install
unchanged; the retained release's `install.log` contains build diagnostics.

To pin a revision and pass options, download the bootstrap from that **full commit
SHA**, then use the same SHA for `--ref`:

```sh
curl -fsSL https://raw.githubusercontent.com/oozoofrog/jev-context-manager/FULL_COMMIT_SHA/install.sh -o /tmp/jcm-install.sh && \
  bash /tmp/jcm-install.sh --ref FULL_COMMIT_SHA
```

Available options:

- `--prefix PATH`: runtime/releases/backups root (default `~/.local/share/jcm`).
- `--bin-dir PATH`: launcher directory (default `~/.local/bin`).
- `--skill-dir PATH`: full skill folder; use this for a custom/legacy Codex skill location.
- `--no-skill`: install only the CLI.
- `--replace-existing`: explicitly back up and replace a conflicting `jcm` launcher
  or `astra-continuity` skill. Unrelated files in those directories remain untouched.
- `--ref REF`: branch, tag or full commit SHA; `JCM_REF` supplies the default.

Managed reinstallation needs no replacement flag. Conflicting unmanaged targets are
refused by default. Keep the same prefix for updates: project profiles registered
through this installer use its stable `bin/jcm` launcher path. Pre-existing
profiles created with another Python environment keep that binding; this installer
does not silently migrate them or rewrite their hook definitions.

The installer is [install.sh](install.sh) plus [scripts/install.py](scripts/install.py).
The Python script also supports `--source /path/to/checkout` for development tests.
Installation verification and its boundaries are recorded in
[the installer validation report](docs/installer-validation.md).

## Development installation

Requires Python 3.11+ on macOS/Linux and Git only when inspecting a Git project. Runtime uses the
Python standard library. Existing-session adoption starts a bounded local transcript
follower; no OS daemon, vector database, extra summarization model or background
model session is installed.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/jcm --repo /absolute/project enable --install-hooks
```

The last command registers only the chosen canonical root, creates a random
repo/worktree identity, and merges project-local `.codex/hooks.json`. Existing
hook definitions remain; original bytes are backed up before changing that file.
An absent `.codex/config.toml` receives a minimal hooks layer. Existing config is
preserved. Existing `.codex/work` is untouched. This project enable command does
not install user-global hooks, skills, model settings or API credentials. The remote
installer above separately installs the user-scoped routing skill.

The default private store is `~/Library/Application Support/JCM` (directories
0700, files 0600). `--home /absolute/private/path` or `JCM_HOME` chooses another
store. Source text and credentials are not placed in the project config.
Storage authorization and Jev egress are separate; **egress is denied by default**.
The `TYPESAFE_API_KEY` environment variable is read only by the provider, never
put in argv, packs, settings or call logs.

Codex must trust the project and review the hook definitions before ordinary
execution. Use the host's `/hooks` review. A JCM install does not grant hook trust.
The live harness uses the documented one-invocation hook-trust bypass only for
the synthetic definitions it just generated and inspected; it does not prove
persisted production hook trust. In this installed CLI, `--ignore-user-config`
also prevented discovery of the project hook layer in the probes.

To opt an already registered project into egress explicitly:

```sh
.venv/bin/jcm --repo /absolute/project policy --egress allow
```

This applies to newly captured events. Previously denied events are not silently
reclassified for egress. The first-slice conservative gate falls back locally if
the candidate batch includes denied content. Real project histories were not
sent during the development pilot: real Jev tests use synthetic project data.

For a session already running before installation, use `bootstrap existing` after
registration; its follower does not assume immediate hook reload. Fresh sessions
use `SessionStart` preparation and the request hook automatically. Both paths are
documented in [bootstrap.md](docs/bootstrap.md).

## Supported commands

All machine output is one UTF-8 JSON object; diagnostics go to stderr. Global
`--repo` and `--home` options precede the command.

| Command | Behavior |
|---|---|
| `enable [--install-hooks] [--allow-jev-egress]` | Register exactly one root; optional explicit egress and hook merge |
| `install-hooks` | Idempotently merge project lifecycle hooks; no implicit trust |
| `hook --stdin` | Validate and durably capture one host event; fixed bootstrap for requests |
| `bootstrap existing [--session-id ID] [--transcript PATH]` | Adopt one existing session, merge hooks, start bounded local tail follower |
| `bootstrap new --request-token ID` | Recover current history through Jev and return the actual pack with read receipt |
| `dispatch --request-token ID` | Recover sources, process bounded queue, call Jev and build query-specific pack |
| `read --pack ID` | Serve immutable pack bytes and record `read_served`, not agent success |
| `inspect --record ID` | Read a cited original source as data |
| `worker drain [--limit N]` | Lease and process bounded semantic work |
| `status`, `doctor` | Report actual observed hooks, queue, coverage and limitations |
| `policy --egress allow\|deny --enabled true\|false` | Explicit epoch change; invalidate earlier packs |
| `disable` | Stop new collection/transmission; preserve records |
| `forget --session ID` | Tombstone a session, delete its admitted records and invalidate derivatives |

Only transcript paths admitted by hooks or explicit existing-session bootstrap are
tailed; JCM does not read every Codex conversation. See [both bootstrap paths](docs/bootstrap.md)
and the source [routing skill](skills/astra-continuity/SKILL.md). The remote installer
installs that skill; editable development installation alone does not. The current parser is gated to the
probed CLI version and public `item_completed` records. Unknown formats remain
visible coverage gaps. Source records distinguish statements, agent reports and
observed tool output. A model label never verifies implementation, resolves an
issue, applies a superseding relationship or authorizes a command.

## Validation

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/live_jev.py
.venv/bin/python scripts/live_e2e.py
.venv/bin/python scripts/live_bootstrap.py
```

The three live scripts explicitly perform real network/model calls. The first
uses synthetic events and is not a new Codex session. The second creates three
real Astra CLI threads with ordinary user prompts. It terminates B immediately
after its correction is committed, then asks fresh C to implement the feature.
C's current request does not contain the random correction marker or the earlier
protocol requirement. An independent harness checks the actual code and output,
Jev audit records, B's pending queue and C's read receipt. No `resume`, fork,
conversation copy or manual record/checkpoint is used.

## Current limits and release gates

- `doctor` intentionally reports `limited` until production trust and remaining
  acceptance gates have been attested. `quality=normal` describes a successful
  bounded Jev dispatch, not a v1 completeness claim.
- Task applicability is currently project-scoped and conservative. Multiple
  independent tasks in one large project, relation expansion beyond the bounded
  candidate set and Korean corpus recall remain evaluation work.
- Candidate and pack ceilings report gaps or block; protected records are not
  silently truncated. Token estimates and financial cost are not fabricated.
  Calls, attempts, exact requests and returned usage are recorded.
- File reconciliation covers at most 1,000 non-symlink files of at most 2 MB each;
  excluded files are reported. Snapshot equality never turns old tests into a
  current PASS. Non-atomic file/Git observation is reported honestly.
- Hosted/specialized tools, actual Desktop reload, automatic compaction, physical
  power failure, full backup/export/restore, version migrations and a production
  uninstall flow remain release gates. Only explicitly probed transcript versions
  are parsed. Unknown/missing sources never become a completeness claim.
- Session deletion blocks recapture of that session and removes known derivatives.
  Independent later statements that quote it are separate sources; arbitrary
  cross-source semantic erasure and restored-backup tombstone propagation require
  the remaining P3 work. Provider-side/SSD physical erasure is not promised.
- The `checkpoint`, `record`, `expand`, `import`, `export`, `repair` and `why`
  commands from the full design are not implemented or simulated. Their absence
  does not replace the implemented automatic capture/fresh-session path.
