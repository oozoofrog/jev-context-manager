#!/usr/bin/env bash
# Remote entry point. All installation logic is in the matching ref's Python script.
set -euo pipefail

main() {
    local ref="${JCM_REF:-main}" python="${JCM_PYTHON:-python3}" arg next_ref=0
    for arg in "$@"; do
        if [ "$next_ref" = 1 ]; then ref="$arg"; next_ref=0; continue; fi
        case "$arg" in
            --ref) next_ref=1 ;;
            --ref=*) ref="${arg#--ref=}" ;;
            --help|-h)
                cat <<'HELP'
Install JCM CLI and the astra-continuity skill for local Codex (macOS/Linux).
Usage: bash install.sh [--ref REF] [--prefix PATH] [--bin-dir PATH]
                       [--skill-dir PATH] [--no-skill] [--replace-existing]
Requires Python 3.11+, venv/pip support, curl and HTTPS access to GitHub/PyPI.
Defaults: ~/.local/share/jcm, ~/.local/bin, ~/.agents/skills/astra-continuity.
JCM_PYTHON selects Python; JCM_REF selects the default Git ref.
No sudo, global Codex config edits, project activation, or Jev calls.
HELP
                return 0 ;;
        esac
    done
    [ "$next_ref" = 0 ] || { echo 'Missing --ref value' >&2; return 2; }
    command -v "$python" >/dev/null || { echo 'Python 3.11+ is required (set JCM_PYTHON).' >&2; return 1; }
    "$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required")'
    command -v curl >/dev/null || { echo 'curl is required.' >&2; return 1; }
    local temp encoded_ref
    temp="$(mktemp -d "${TMPDIR:-/tmp}/jcm-installer.XXXXXX")"
    trap "rm -rf -- $(printf '%q' "$temp")" EXIT
    encoded_ref="$("$python" -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$ref")"
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --connect-timeout 20 --max-time 120 \
        "https://raw.githubusercontent.com/oozoofrog/jev-context-manager/${encoded_ref}/scripts/install.py" \
        --output "$temp/install.py"
    "$python" "$temp/install.py" "$@"
    rm -rf -- "$temp"
    trap - EXIT
}

main "$@"
