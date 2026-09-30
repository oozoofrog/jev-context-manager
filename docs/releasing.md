# Release and install

Run the maintained pipeline instead of repeating release operations in chat:

```sh
.venv/bin/python scripts/release.py \
  --publish --install \
  --notes docs/releases/1.0.2.md \
  --commit-message "Release JCM 1.0.2" \
  --scope-file .task-notes/release-scope.json \
  --asset evidence/continuity-consumer-1.0.2.json
```

Use the current version's notes file for later releases. Update the Python package,
runtime and plugin versions together, and regenerate the bundle with
`python scripts/build_plugin.py`. Python 3.11+, Git, authenticated `gh`, and the
Codex CLI are required. Installation registers an absent Git-backed `jcm` marketplace tracking `main`,
or upgrades the existing canonical marketplace. Its revision must equal the
validated release commit before installation.

Without `--publish`, the script only validates and builds. `--install` requires
`--publish`; `--scope-file` supplies a reviewed JSON list of exact repository-relative paths
allowed in the commit; other dirty files and untracked drafts are preserved. The
staged path set and credential scan must pass before committing. Without a scope
file, pending tracked changes and new source/docs/tests remain the default. Untracked evidence is included only by exact
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
6. Uploads the wheel, public lifecycle evidence and explicit public assets, then publishes the release. An
   existing asset must have the same hash; it is never overwritten silently.
7. Backs up user configuration, plugin inventory/cache and external JCM storage.
   SQLite uses online backup, preserving committed WAL contents; source-bearing
   blobs are copied after the database snapshot.
8. Updates the marketplace/plugin and checks the installed version, runtime hashes,
   unchanged unrelated settings/other plugins, and skill exposure in a fresh app-server
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

Local installation permits only the JCM marketplace and plugin configuration
entries to change. Each CLI write compares fresh before/after unrelated settings;
a conflicting change stops verification without restoring an old whole config.
Whole-file and interval hashes are observations, not a reason to overwrite concurrent
user edits. The existing-marketplace path is also exercised in the published
fixture's isolated Codex/JCM homes; its backup never accesses the user's store.
