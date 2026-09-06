#!/usr/bin/env bash
# Update an installed srrverify systemd service to another release, rolling back
# automatically if the new version fails its post-restart health check.
#
#   sudo update.sh [--version vX.Y.Z] [--rollback] [--force] [--from-app]
#
# --version   target a specific tag (default: the latest published release).
# --rollback  swap back to the previous release and restart.
# --force     reinstall even if already on the target version.
# --from-app  the running app made this call. On this path the target is always
#             the latest release (--version is ignored) and the script must
#             re-launch itself into a transient systemd unit so it survives the
#             restart of the service that invoked it.
#
# The prefix is fixed by this script's own install location (<prefix>/bin) and
# is deliberately NOT an option: on the --from-app path the caller is the
# unprivileged service user via a pinned sudoers rule.
set -euo pipefail

_self="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
case "$_self" in
  */bin) PREFIX="$(dirname "$_self")" ;;
  *) PREFIX="/opt/srrverify" ;; # running straight from a source checkout
esac

VERSION=""
ROLLBACK=0
FORCE=0
FROM_APP=0
KEEP=3

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --rollback) ROLLBACK=1; shift ;;
    --force) FORCE=1; shift ;;
    --from-app) FROM_APP=1; shift ;;
    -h | --help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }

# The app never pins a version; force "latest" so the sudoers rule can be an
# exact-argument match with no wildcard.
[ "$FROM_APP" -eq 1 ] && VERSION=""

# --- Re-launch detached, so `systemctl restart srrverify` below cannot kill us
#     along with the service that spawned this script. -------------------------
if [ "${_SRRVERIFY_DETACHED:-0}" != "1" ]; then
  reexec=("$0")
  [ -n "$VERSION" ] && reexec+=(--version "$VERSION")
  [ "$ROLLBACK" -eq 1 ] && reexec+=(--rollback)
  [ "$FORCE" -eq 1 ] && reexec+=(--force)
  [ "$FROM_APP" -eq 1 ] && reexec+=(--from-app)
  if command -v systemd-run >/dev/null 2>&1; then
    exec systemd-run --quiet --collect \
      --unit="srrverify-update-$$-$(date +%s)" \
      --setenv=_SRRVERIFY_DETACHED=1 \
      --setenv=GAMECRC_REPO="${GAMECRC_REPO:-}" \
      --setenv=GH_TOKEN="${GH_TOKEN:-}" \
      "${reexec[@]}"
  elif [ "$FROM_APP" -eq 1 ]; then
    echo "systemd-run is required for an in-app update but is not installed" >&2
    exit 1
  else
    echo "warning: systemd-run not found; running attached — the restart below" >&2
    echo "         may interrupt this script. Install systemd-run or use --rollback." >&2
  fi
fi

export PREFIX
if [ -r "${PREFIX}/bin/lib.sh" ]; then
  # shellcheck source=scripts/lib.sh
  . "${PREFIX}/bin/lib.sh"
else
  # shellcheck source=scripts/lib.sh
  . "$(cd "$(dirname "$(readlink -f "$0")")" && pwd)/lib.sh"
fi

mkdir -p "${PREFIX}/data"
LOG="${PREFIX}/data/update.log"
exec >>"$LOG" 2>&1
echo "=== $(date -Is) update run (pid $$, detached=${_SRRVERIFY_DETACHED:-0}) ==="

need curl
need tar
need sha256sum
need systemctl
need flock

# One update at a time: guard the shared venv and the `current` symlink.
exec 9>"${PREFIX}/data/update.lock"
flock -n 9 || die "another update is already running"

# Health URL from the unit's own environment (fall back to loopback:8000).
_env="$(systemctl show -p Environment srrverify.service 2>/dev/null || true)"
h_host="$(printf '%s\n' "$_env" | tr ' ' '\n' | sed -n 's/^GAMECRC_HOST=//p')"
h_port="$(printf '%s\n' "$_env" | tr ' ' '\n' | sed -n 's/^GAMECRC_PORT=//p')"
[ -n "$h_host" ] && [ "$h_host" != "0.0.0.0" ] || h_host="127.0.0.1"
[ -n "$h_port" ] || h_port="8000"
HEALTH_URL="http://${h_host}:${h_port}/api/health"

current_target="$(readlink -f "${PREFIX}/current" 2>/dev/null || true)"
svc_user="$(stat -c '%U' "${PREFIX}/data" 2>/dev/null || echo srrverify)"

own_current() { chown -h "${svc_user}:${svc_user}" "${PREFIX}/current" 2>/dev/null || true; }

# activate <release_dir> <want_version|"">
#   Point `current` at the dir, sync the venv to its requirements, restart, and
#   poll /api/health for up to 30s. With a version, the reported `version` must
#   match; without one, HTTP 200 is enough.
activate() {
  local dir="$1" want="$2" body i
  ensure_venv "${dir}/backend/requirements.txt"
  swap_current "$dir"
  own_current
  systemctl restart srrverify.service
  for i in $(seq 1 30); do
    sleep 1
    body="$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null || true)"
    [ -n "$body" ] || continue
    [ -z "$want" ] && return 0
    case "$body" in
      *"\"version\":\"${want}\""* | *"\"version\": \"${want}\""*) return 0 ;;
    esac
  done
  return 1
}

if [ "$ROLLBACK" -eq 1 ]; then
  prev="$(ls -1dt "${PREFIX}"/releases/*/ 2>/dev/null |
    sed 's#/$##' | grep -vxF "$current_target" | head -1 || true)"
  [ -n "$prev" ] || die "no previous release to roll back to"
  log "rolling back to $(basename "$prev")"
  activate "$prev" "" || die "rollback restart failed - check: journalctl -u srrverify"
  log "rolled back to $(basename "$prev")"
  exit 0
fi

[ -n "$VERSION" ] || VERSION="$(latest_tag)"
[ -n "$VERSION" ] || die "could not determine a target version"
ver="${VERSION#v}"
REL_DIR="${PREFIX}/releases/${ver}"

running_ver="$(cat "${PREFIX}/current/VERSION" 2>/dev/null || echo unknown)"
if [ "$running_ver" = "$ver" ] && [ "$FORCE" -eq 0 ]; then
  log "already on ${ver}; nothing to do"
  exit 0
fi

[ "$FROM_APP" -eq 1 ] && log "update requested from the app UI"
log "updating ${running_ver} -> ${ver}"

[ -d "$REL_DIR" ] || download_release "$VERSION" "$REL_DIR"
chown -R "${svc_user}:${svc_user}" "$REL_DIR"

# Refresh the out-of-tree helper copies (effective on the next run).
install -m 0755 -o root -g root "${REL_DIR}/scripts/lib.sh" "${PREFIX}/bin/lib.sh"
install -m 0755 -o root -g root "${REL_DIR}/scripts/update.sh" "${PREFIX}/bin/update.sh"

if activate "$REL_DIR" "$ver"; then
  log "now running ${ver}"
  prune_releases "$KEEP"
  exit 0
fi

log "health check failed on ${ver}; rolling back"
[ -n "$current_target" ] || die "new version unhealthy and no prior release recorded"
activate "$current_target" "" || die "rollback also failed - check: journalctl -u srrverify"
die "update to ${ver} failed its health check; rolled back to $(basename "$current_target")"
