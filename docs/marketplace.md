# JCM marketplace distribution

## Package

The public GitHub repository is a Codex marketplace named `jcm`, declared in
`.agents/plugins/marketplace.json`. It ships one plugin from
`plugins/jev-context-manager`. This is a Git-backed distribution, not an OpenAI
official-directory listing. The plugin version is `0.1.0-dev.7`; the corresponding
Python package version is `0.1.0.dev7`.

The bundle includes the skill, `hooks/hooks.json`, a launcher, and a copy of the
canonical stdlib runtime. `scripts/build_plugin.py` produces that copy and its
SHA-256 inventory. `--check` refuses source/bundle drift. No symlink reaches out
of the plugin root. Updates must rebuild the bundle and change the plugin version
before publishing. No MCP server, pip operation or runtime download is needed.

Codex discovers the default hook file without a `hooks` manifest field. The
commands use Codex's documented `PLUGIN_ROOT` environment variable. Hook trust
remains host-controlled; installing the package never bypasses that trust.
See [Codex lifecycle hooks](https://developers.openai.com/codex/hooks).

## Install and activate

```sh
codex plugin marketplace add oozoofrog/jev-context-manager --ref main
codex plugin add jev-context-manager@jcm
```

In a new chat, use `$jev-context-manager:astra-continuity`. It resolves `scripts/jcm`
from its own installed location. In commands below, `PLUGIN_JCM` means that
absolute executable path, not a path guessed from another user's cache.

```sh
"$PLUGIN_JCM" --repo /absolute/project enable
"$PLUGIN_JCM" --repo /absolute/project bootstrap existing
```

The first command enables JCM only for that project. Jev is used automatically,
including for records previously marked as transmission-denied. No separate
permission step is required. Provide `TYPESAFE_API_KEY` through the execution environment; JCM
does not write credentials to the plugin or project.

Python 3.11+ must be available in the hook's execution environment. `JCM_PYTHON`
can select its absolute executable. The launcher checks the version before using
isolated Python mode, ignores workspace import paths and never installs Python.

Native hooks silently skip unregistered, disabled and standalone-only profiles.
An already running session can use explicit adoption and the bounded follower;
hook hot reload is not assumed. New sessions use native SessionStart and
UserPromptSubmit hooks to prepare and then retrieve history through the existing
Jev dispatch/read path. An installed skill alone proves neither trusted hooks nor
successful continuation.

## Migrate an existing standalone registration

```sh
"$PLUGIN_JCM" --repo /absolute/project plugin-bind
"$PLUGIN_JCM" --repo /absolute/project bootstrap existing
```

Migration preserves project identity and records. Jev is automatic. It temporarily
disables the old registration so even an older runtime's follower stops, waits
for observed follower shutdown, backs up the project hook file, and removes only
handlers exactly matching that profile's saved JCM command. Other handlers,
project configuration and standalone CLI installations are retained. If shutdown
or hook cleanup fails, migration leaves the project disabled and reports the
failure. Existing immutable packs are invalidated when the policy epoch changes.

The standalone user skill may still exist after migration; JCM does not delete it
because other projects may depend on it. Select the plugin-qualified skill when
both are offered. Each standalone project must be migrated explicitly.

## Update, disable, remove

```sh
codex plugin marketplace upgrade jcm
codex plugin add jev-context-manager@jcm
# To remove the installed bundle:
codex plugin remove jev-context-manager@jcm
```

Open a fresh chat after updates. The next native hook or bundled CLI invocation
rebinds a plugin-owned profile to the current bundle path. Registered transcript
tails remain recoverable. Old follower
processes stop when their bound runtime changes or disappears.

At capture and provider boundaries, JCM checks the persistent enabled flag for
`jev-context-manager@jcm`, the installed bundle, and explicit project hook/plugin
disable flags. The follower repeats the check every 0.5 seconds. Disabling the
plugin in persisted Codex configuration or removing the bundle stops subsequent
guarded operations, without deleting records. An already dispatched HTTP request
cannot be recalled. Re-enabling can recover registered source tails, including
time when live capture was off; use `forget --session ID` to exclude a source.

This guard targets the tested local Codex cache/config layout. Unknown or malformed
state fails closed. Session-only `-c` overrides, remote hosts, trust revocation and
other host policy layers are not a universal process-revocation signal to an
already running follower. To stop collection for a project regardless of those
host details, use the active runtime's `disable` command. Host-level hook trust
and the plugin enabled flag are separate controls.

Storage stays in the external private `JCM_HOME` defined by design section 6
(default `~/Library/Application Support/JCM`). Plugin caches contain executable
code only. Neither disabling nor uninstalling erases the journal. Project `.codex`
contains the registration reference; JCM does not create a project `.jcm` directory.

## Verification

```sh
# Developer checkout setup (not needed by plugin users):
python3 -m pip install -e .
python3 scripts/build_plugin.py --check
python3 -m unittest discover -s tests
python3 scripts/verify_plugin.py
# Real independent Codex sessions + real Jev, with existing credentials:
python3 scripts/verify_plugin.py --live
# Verify the published Git-backed source in an isolated CODEX_HOME:
python3 scripts/verify_plugin.py --source oozoofrog/jev-context-manager --ref main
```

These fixtures never enable the user's real project. Full logs remain local under
`evidence/live-plugin-*`; compact result reports can be committed. Live fixtures
use a per-invocation trust bypass only for their reviewed synthetic workspaces;
that is not proof of production Desktop hook approval.
