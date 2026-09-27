# Remote installer validation — 2026-09-27

The user-scoped remote installer ships in `install.sh` and `scripts/install.py`.
It installs a versioned virtual environment, stable launcher and local Codex skill.
Project activation, hook trust and Jev egress remain explicit, separate operations.
Context storage still follows design section 6: external private `JCM_HOME`, with
only project identity/config references in `.codex/jcm.json`.

## Validation lanes

| Lane | Result | Evidence |
|---|---|---|
| Existing runtime + installer regression tests | 53/53 PASS | `evidence/installer-tests.log` |
| Local source, actual venv/pip install and upgrade | PASS | `evidence/installer-local-smoke.json` and `.log` |
| Downloaded GitHub bootstrap and source | Pending the publication verification below | Separate remote smoke evidence |
| Actual Codex fresh-session skill discovery | NOT TESTED | Skill-file installation is not proof of host discovery |
| Actual Jev in this installer test | NOT RUN | No Jev requests; earlier continuity reports remain separate |

The real install smoke uses isolated runtime, bin, skill and private-data paths,
including spaces. It registers a synthetic project and saves its hook configuration,
then reinstalls JCM into a new virtual environment. The original project profile
and hook file remain byte-identical. Executing the previously saved hook through
the stable launcher records a new event and returns the fresh-session bootstrap.
The earlier release remains available; the synthetic project is disabled afterward.
The observed user-global Codex config/hook hashes and an unrelated skill are unchanged.

The test suite covers refusal of conflicting unmanaged targets, explicit replacement
with backups, ordinary activation-failure rollback, build-failure preservation,
CLI-only installation, destination overlap rejection, archive traversal/link rejection,
immutable commit resolution before archive download, and stable runtime binding.

## Corrections found during implementation

- Python on this macOS host resolves the executable through a `current` symlink to
  a specific release. Registering that executable would pin hooks to the old venv.
  Managed wrappers now export their stable `JCM_LAUNCHER`; new profiles retain that
  launcher. Non-installer runtime binding remains unchanged. The real upgrade smoke
  verifies the old hook still works after installing a new release.
- Initial unit assertions compared macOS `/var` and `/private/var` paths literally.
  The fixtures now use canonical paths. The initial failure log is retained in
  `evidence/installer-tests-initial-path-failure.log`.

## Operational boundaries

No global Codex configuration, model selection, credential, shell profile or hook-trust
setting is changed. Installed skills follow the documented user discovery directory:
[Codex local skill locations](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills).
Actual host discovery and ordinary project hook trust require their own observation.
No production JCM installation was performed on the developer's machine by these tests.

The installer retains previous releases and path-indexed backups. It restores replaced
paths on ordinary activation failures; sudden process termination or power loss is not
an atomic multi-path transaction. It does not automatically prune releases/backups or
migrate project profiles that were originally bound to a different unmanaged Python.
