# Release and install

Run the maintained pipeline instead of repeating release operations in chat:

```sh
.venv/bin/python scripts/release.py \
  --publish --install \
  --notes docs/releases/v0.1.0-dev.8.md \
  --commit-message "Remove local Jev quotas and automate release installation" \
  --include evidence/provider-limits-live.json \
  --include evidence/provider-limits-tests.log \
  --include evidence/provider-limits-plugin-validation.json
```

Use the current version's notes file for later releases. Update the Python package,
runtime and plugin versions together, and regenerate the bundle with
`python scripts/build_plugin.py`. Python 3.11+, Git, authenticated `gh`, and the
Codex CLI are required. Installation expects the already-configured Git-backed
`jcm` marketplace and an existing enabled user installation.

Without `--publish`, the script only validates and builds. `--install` requires
`--publish`; `--commit-message` explicitly includes pending tracked changes and
new source/docs/tests in the commit. Untracked evidence is included only by exact
`--include` paths. Raw fixture logs and `.task-notes` stay local. Review pending
changes before running a release; this script cannot decide whether unrelated
edits in source files belong to the release.

The pipeline:

1. Checks repository, branch, matching versions, source fingerprint and notes.
2. Verifies the bundle, runs all regression tests and builds a source-matching wheel.
3. Commits the selected inputs, creates an immutable version tag and atomically
   pushes branch plus tag. It never force-pushes or moves an existing tag.
4. Requires successful CI for that exact commit.
5. Installs the published Git commit in an isolated Codex home and runs lifecycle
   checks. This does not create AI agent sessions or issue Jev requests.
6. Uploads the wheel and lifecycle evidence, then publishes the prerelease. An
   existing asset must have the same hash; it is never overwritten silently.
7. Backs up user configuration, plugin inventory/cache and external JCM storage.
   SQLite uses online backup, preserving committed WAL contents; source-bearing
   blobs are copied after the database snapshot.
8. Updates the marketplace/plugin and checks the installed version, runtime hashes,
   unchanged configuration/other plugins, and skill exposure in a fresh app-server
   process. It does not claim hot reload of an already-running chat.

Each completed stage emits one short JSON line. Full logs, wheel, progress and
`result.json` are in `.task-notes/releases/<tag>-<timestamp>/`. These files are local,
private and Git-ignored. Failures return a nonzero exit status and the exact log
path. Remote effects already completed (such as a push) are recorded, not silently
rolled back.

To resume, repeat the same command with:

```sh
--resume .task-notes/releases/<tag>-<timestamp>
```

Unchanged inputs reuse successful tests/builds and the published fixture. Remote
commit/tag/CI/release state and user installation are still checked. Changed
source or release-note contents are rejected for that checkpoint; start a fresh
run and, if a tag was already published, choose a new version. Backups are retained
under `$CODEX_HOME/backups/`; after journal schema upgrades, restoring just an older
executable is not a database downgrade.
