#!/bin/bash
# Ringer engine wrapper: run OpenCode under a macOS Seatbelt sandbox.
#
# OpenCode has no OS-level sandbox of its own — its --dangerously-skip-permissions
# flag (required for headless runs) disables ALL of its interactive approval
# prompts. This wrapper supplies the real containment: full network; reads open
# EXCEPT a deny-list of credential/secret paths (see DENY_READ_CANDIDATES below);
# writes confined to the task dir, a per-run scratch/cache dir, and OpenCode's
# own state dirs.
#
# Usage (as a ringer engine bin):
#   opencode-sandboxed.sh <taskdir> [--no-sandbox] <opencode args...>
#
# The first argument is the task directory (pass "{taskdir}" first in
# args_template). "--no-sandbox" as the second argument skips Seatbelt entirely
# — wire it as the engine's full_access_args so ringer's allow_full_access gate
# still applies. macOS only (sandbox-exec); on other platforms only
# --no-sandbox mode works.
set -euo pipefail

TASKDIR="${1:?usage: opencode-sandboxed.sh <taskdir> [--no-sandbox] <args...>}"; shift
SANDBOX=1
if [ "${1:-}" = "--no-sandbox" ]; then SANDBOX=0; shift; fi

# Resolve opencode without tripping `set -e` (command -v returns nonzero when absent).
if ! OPENCODE_BIN="$(command -v opencode)" || [ -z "$OPENCODE_BIN" ]; then
  echo "opencode-sandboxed.sh: opencode not found on PATH" >&2
  exit 127
fi

if [ "$SANDBOX" = "0" ]; then
  exec "$OPENCODE_BIN" "$@" < /dev/null
fi

if [ ! -x /usr/bin/sandbox-exec ]; then
  echo "opencode-sandboxed.sh: /usr/bin/sandbox-exec not available (macOS only)." >&2
  echo "Use the engine's full-access mode (--no-sandbox) or add your own sandbox." >&2
  exit 1
fi

TASKDIR_REAL="$(cd "$TASKDIR" && pwd -P)"

# Per-run scratch root — becomes both TMPDIR and XDG_CACHE_HOME for OpenCode, so
# we never have to open all of /private/tmp or ~/.cache to the sandboxed agent.
# Resolve to the real path (/var/folders symlinks to /private/var/folders);
# Seatbelt subpath matching needs the canonical path or writes EPERM-crash.
SCRATCH="$(cd "$(mktemp -d -t ringer-opencode-scratch)" && pwd -P)"
PROFILE="$(mktemp -t ringer-opencode-prof)"
cleanup() { rm -rf "$SCRATCH" "$PROFILE"; }
trap cleanup EXIT

# Paths are passed to the profile via sandbox-exec -D parameters, NOT string
# interpolation — a task dir containing quotes/parens/newlines can't inject rules.
cat > "$PROFILE" <<'SBEOF'
(version 1)
(allow default)
(deny file-write*)
(allow file-write*
  (subpath (param "TASKDIR"))
  (subpath (param "SCRATCH"))
  (subpath (param "OC_SHARE"))
  (subpath (param "OC_STATE"))
  (subpath (param "OC_CONFIG")))
; /dev is needed for /dev/null, /dev/urandom, etc.; writes there can't create
; persistent files without root, so a few literals are allowed rather than via param.
(allow file-write-data
  (literal "/dev/null")
  (literal "/dev/dtracehelper")
  (literal "/dev/tty"))
SBEOF

# Read-surface hardening. The worker keeps network and broad filesystem reads
# (it must read repos and call APIs), but nothing a worker does should require
# reading the operator's credentials. Seatbelt evaluates rules in order and the
# LAST match wins, so denies appended after `(allow default)` close the read
# hole for the paths below. Values reach the profile as -D parameters, never
# interpolated into profile text, so a path containing quotes or parens cannot
# inject rules. Missing paths are skipped.
#
# Machine-local additions (cloud drives, work directories, extra key stores):
#   - one path per line in the file selected by RINGER_OPENCODE_DENY_READ_FILE
#     (default ~/.config/ringer/opencode-deny-read.txt; '#' comments and leading
#     '~' both supported), or
#   - RINGER_OPENCODE_DENY_READ, colon-separated.
DENY_READ_CANDIDATES=(
  "$HOME/.ssh"
  "$HOME/.aws"
  "$HOME/.gnupg"
  "$HOME/.secrets"
  "$HOME/.netrc"
  "$HOME/.npmrc"
  "$HOME/.config/gh"
  "$HOME/.config/gcloud"
  "$HOME/.codex"
  "$HOME/.claude"
  "$HOME/Library/Keychains"
)
DENY_READ_FILE="${RINGER_OPENCODE_DENY_READ_FILE:-$HOME/.config/ringer/opencode-deny-read.txt}"
if [ -f "$DENY_READ_FILE" ]; then
  while IFS= read -r _line || [ -n "$_line" ]; do
    case "$_line" in ''|'#'*) continue ;; esac
    DENY_READ_CANDIDATES+=("${_line/#\~/$HOME}")
  done < "$DENY_READ_FILE"
fi
if [ -n "${RINGER_OPENCODE_DENY_READ:-}" ]; then
  _saved_ifs="$IFS"; IFS=':'
  for _extra in $RINGER_OPENCODE_DENY_READ; do
    [ -n "$_extra" ] && DENY_READ_CANDIDATES+=("${_extra/#\~/$HOME}")
  done
  IFS="$_saved_ifs"
fi

DENY_ARGS=()
_deny_rules=""
_deny_idx=0
_deny_seen="|"

# Seatbelt matches the path as PRESENTED, not the symlink-resolved path
# (verified on macOS: with only realpath(p) denied, a cloud-drive symlink like
# "~/OneDrive" -> ~/Library/CloudStorage/OneDrive-... stays readable through
# the symlinked spelling). So deny BOTH the path as written and its resolved
# target; denying only one leaves the other as a bypass.
# subpath covers directories and their contents; literal covers plain files.
_add_deny() {
  case "$_deny_seen" in *"|$1|"*) return 0 ;; esac
  _deny_seen="${_deny_seen}$1|"
  DENY_ARGS+=( -D "DENY_READ_${_deny_idx}=$1" )
  _deny_rules="${_deny_rules}  (subpath (param \"DENY_READ_${_deny_idx}\"))
  (literal (param \"DENY_READ_${_deny_idx}\"))
"
  _deny_idx=$((_deny_idx + 1))
  return 0
}

for _p in "${DENY_READ_CANDIDATES[@]}"; do
  [ -e "$_p" ] || continue
  _add_deny "$_p"
  _real="$(/bin/realpath "$_p" 2>/dev/null || true)"
  if [ -n "$_real" ] && [ "$_real" != "$_p" ]; then _add_deny "$_real"; fi
done
if [ "$_deny_idx" -gt 0 ]; then
  {
    printf '(deny file-read*\n'
    printf '%s' "$_deny_rules"
    printf ')\n'
  } >> "$PROFILE"
fi

export TMPDIR="$SCRATCH"
export XDG_CACHE_HOME="$SCRATCH/cache"
mkdir -p "$XDG_CACHE_HOME"

# Run as a child (not exec) so the EXIT trap fires and cleans up the profile +
# scratch dir even on the success path; propagate the child's exit status.
set +e
/usr/bin/sandbox-exec \
  -D "TASKDIR=$TASKDIR_REAL" \
  -D "SCRATCH=$SCRATCH" \
  -D "OC_SHARE=$HOME/.local/share/opencode" \
  -D "OC_STATE=$HOME/.local/state/opencode" \
  -D "OC_CONFIG=$HOME/.config/opencode" \
  ${DENY_ARGS[@]+"${DENY_ARGS[@]}"} \
  -f "$PROFILE" "$OPENCODE_BIN" "$@" < /dev/null
status=$?
set -e
exit "$status"
