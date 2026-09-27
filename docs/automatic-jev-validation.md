# Automatic Jev usage — dev.7

The 2026-09-28 user request removes JCM's separate Jev permission procedure.
Enabled projects use Jev automatically. Legacy project and event denial flags
no longer gate classification, retrieval or correction comparison. Old event
contents and identities are preserved; reimport and consent migration are not
required. The status field `allow_egress` remains `true` for compatibility.

The CLI permission flags and skill consent instructions were removed. The original
v1.0 design and earlier validation reports retain their historical policy;
this release supersedes that policy. Project/plugin disablement, source scope,
redaction, deletion, epoch checks and call budgets remain effective.

Validation:

- 90 regression tests passed, including default registration, unchanged legacy
  records reaching both classification and retrieval, correction comparison,
  missing-key fallback, disabled plugins/projects, deletion and page delivery.
  See [test log](../evidence/automatic-jev-tests.log).
- Real Jev validation used an isolated synthetic fixture with legacy denial flags
  and no permission step. Three HTTP calls succeeded (4,169, 4,399 and 4,464 bytes);
  recovery was `normal` and identified the correction. See
  [live result](../evidence/automatic-jev-live.json).
- Bundle integrity, plugin/skill validation and the Python wheel build passed.

`degraded` still identifies missing or unusable Jev judgments, including credential,
transport, payload and budget failures. It is no longer caused by a JCM permission
setting. This validation does not attest independent-agent task continuation or
Desktop hook approval.
