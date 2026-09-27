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
enhancements; those were separately covered by the 63-test source run. Published
Git-backed verification is recorded below after deployment.

## Evidence boundaries

Tests use isolated `CODEX_HOME` and synthetic project history. Live session tests
use a per-invocation hook trust bypass for the reviewed fixture only. The user's
real projects were not enabled or ingested. Desktop plugin-card display, interactive
hook trust approval and existing Desktop-chat hot reload are not established by
these CLI tests. Compaction/interrupt and other incomplete original acceptance
gates remain incomplete. Package installation is not a v1 production-readiness
claim or an OpenAI official-directory listing.
