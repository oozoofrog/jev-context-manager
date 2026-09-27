# Marketplace validation — 2026-09-27

The marketplace is `jcm`; the plugin is `jev-context-manager`, version
`0.1.0-dev.4`. Runtime source version: `0.1.0.dev4`. This is a prerelease and
does not change the original design's incomplete acceptance gates.

## Source and regression checks

- Plugin manifest validation and skill validation passed.
- Generated runtime/skill payload matches the canonical source and SHA-256 index.
- 63 unit/regression tests passed, including the prior 53 runtime/installer tests
  and 10 plugin tests. Log: `evidence/plugin-regression.log`.
- Coverage includes migration preserving records/policy/unrelated handlers,
  backup creation, stopping an old follower, persistent plugin disable, removed
  cache, malformed settings, project disable, custom external home references,
  inactive-plugin diagnostics, upgrade rebinding and isolated imports.

## Local marketplace and actual host execution

`evidence/live-plugin-1f564da2/result.json` records a successful local-marketplace
installation and two independent real `gpt-6-astra` / `xhigh` CLI sessions, using
Codex `0.158.0-alpha.2.1`. Native plugin hooks captured old-session requirements
and a random tool-output marker. The fresh session received a context pack through
`bootstrap new`, preserved paused true/false behavior and protocol version 1,
and reproduced the exact marker in its saved result. Six real Jev transport calls
succeeded and the fresh pack's quality was `normal`.

The same fixture verified follower shutdown on persistent disable, blocked
capture while disabled, removal of the installed cache, preserved journal on
uninstall, and successful reinstall with preserved records. No project hook file
was added. The user's global configuration was unchanged and the fixture was
disabled afterward.

This first live run preceded the final custom-home-reference and inactive-status
enhancements; those were covered by the 63-test source run and the subsequent
published-package run below.

## Published Git-backed package

`evidence/live-plugin-34657e6c/result.json` records installation from
`oozoofrog/jev-context-manager --ref main`, resolved to source commit
`b1e64813f3fa42670a8ffb1de29472b7ce868765`. All 22 checks passed. It repeated the
two independent real Codex sessions, native hook capture, exact random-marker
restoration, paused/protocol preservation, and six successful real Jev calls.
The installed bundle's SHA-256 inventory matched every included runtime/skill
file. Later commits update tests/documentation/evidence without changing that
runtime payload.

This run also repeated disable, follower shutdown, uninstall, preserved records
and reinstall using the Git-sourced package. Its temporary project was disabled
afterward; the user's global configuration was unchanged.

A separate fresh app-server `skills/list` response exposed the enabled skill as
`jev-context-manager:astra-continuity`, with plugin ID `jev-context-manager@jcm`
and its path inside the installed Git-sourced bundle. Evidence:
`evidence/plugin-skill-discovery.json`. This confirms fresh-process loader
discovery, not the Desktop plugin-card appearance.

## Linux CI and standalone package

[Linux/Python 3.11 CI](https://github.com/oozoofrog/jev-context-manager/actions/runs/36327065433)
passed the bundle consistency check and all 63 tests on commit `1d5c123`.
The first CI run failed because an older transcript rotation fixture assumed
unlink/create always allocates a different inode. Linux can reuse that inode.
The fixture now retains the rotated file and asserts distinct inodes before
checking source-reference deduplication. Runtime behavior was not changed to
satisfy the test. Both initial failure and successful logs are retained in
`evidence/plugin-ci-initial-failure.log` and `evidence/plugin-ci-success.log`.

The standalone `0.1.0.dev4` wheel also built successfully with standard PEP 517
build isolation (`evidence/plugin-wheel-build.log`). An initial non-isolated
build could not import the build backend in the development venv; its log remains
at `evidence/plugin-wheel-without-build-isolation.log`. Plugin installation does
not use either wheel-building path.

## Evidence boundaries

Tests use isolated `CODEX_HOME` and synthetic project history. Live session tests
use a per-invocation hook trust bypass for the reviewed fixture only. The user's
real projects were not enabled or ingested. Desktop plugin-card display, interactive
hook trust approval and existing Desktop-chat hot reload are not established by
these CLI tests. Compaction/interrupt and other incomplete original acceptance
gates remain incomplete. Package installation is not a v1 production-readiness
claim or an OpenAI official-directory listing.
