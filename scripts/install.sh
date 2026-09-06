#!/usr/bin/env bash
# First-time install of srrverify as a systemd service on headless Linux.
#
#   sudo ./install.sh [--version vX.Y.Z] [--prefix /opt/srrverify]
#                     [--user srrverify] [--host 127.0.0.1] [--port 8000]
#
# Safe to re-run: it repairs the venv, the unit file and the sudoers drop-in.
# Use update.sh for version bumps once installed.
set -euo pipefail

PREFIX="/opt/srrverify"
SVC_USER="srrverify"
HOST="127.0.0.1"
PORT="8000"
VERSION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --user) SVC_USER="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -h | --help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }

export PREFIX
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
LIB_TMP=""
if [ -n "$SCRIPT_SOURCE" ] && [ -f "$SCRIPT_SOURCE" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"
  LIB_PATH="${SCRIPT_DIR}/lib.sh"
elif [ -f "scripts/lib.sh" ]; then
  SCRIPT_DIR="$(cd scripts && pwd)"
  LIB_PATH="${SCRIPT_DIR}/lib.sh"
else
  command -v curl >/dev/null 2>&1 || {
    printf '[srrverify] error: missing required command: curl\n' >&2
    exit 1
  }
  LIB_TMP="$(mktemp)"
  trap 'rm -f "$LIB_TMP"' EXIT
  LIB_PATH="$LIB_TMP"
  curl -fsSL \
    "https://raw.githubusercontent.com/${GAMECRC_REPO:-FlightlessWeasel/srrverify}/master/scripts/lib.sh" \
    -o "$LIB_PATH"
fi
# shellcheck source=scripts/lib.sh
. "$LIB_PATH"

need curl
need tar
need sha256sum
need systemctl
need python3
need visudo

if ! id "$SVC_USER" >/dev/null 2>&1; then
  log "creating system user ${SVC_USER}"
  useradd --system --home-dir "$PREFIX" --shell /usr/sbin/nologin "$SVC_USER"
fi

mkdir -p "${PREFIX}/releases" "${PREFIX}/data" "${PREFIX}/bin"

[ -n "$VERSION" ] || VERSION="$(latest_tag)"
[ -n "$VERSION" ] || die "no --version given and no published release found"

REL_DIR="${PREFIX}/releases/${VERSION#v}"
[ -d "$REL_DIR" ] || download_release "$VERSION" "$REL_DIR"

# The privileged helper lives outside the versioned tree so the sudoers path
# stays constant across updates. Owned by root, not writable by the service user.
install -m 0755 -o root -g root "${REL_DIR}/scripts/lib.sh" "${PREFIX}/bin/lib.sh"
install -m 0755 -o root -g root "${REL_DIR}/scripts/update.sh" "${PREFIX}/bin/update.sh"

ensure_venv "${REL_DIR}/backend/requirements.txt"
swap_current "$REL_DIR"

chown -R "${SVC_USER}:${SVC_USER}" "${PREFIX}/releases" "${PREFIX}/data"
chown -h "${SVC_USER}:${SVC_USER}" "${PREFIX}/current"

sed -e "s|@PREFIX@|${PREFIX}|g" \
  -e "s|@USER@|${SVC_USER}|g" \
  -e "s|@HOST@|${HOST}|g" \
  -e "s|@PORT@|${PORT}|g" \
  "${REL_DIR}/systemd/srrverify.service" >/etc/systemd/system/srrverify.service

# Passwordless call to the one privileged helper, with EXACTLY the arguments the
# app uses — no wildcard, no other command. Validate before installing so a bad
# drop-in never lands in /etc/sudoers.d (that would break sudo host-wide).
sudoers_tmp="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: %s/bin/update.sh --from-app\n' "$SVC_USER" "$PREFIX" \
  >"$sudoers_tmp"
if visudo -cf "$sudoers_tmp" >/dev/null; then
  install -m 0440 -o root -g root "$sudoers_tmp" /etc/sudoers.d/srrverify
  rm -f "$sudoers_tmp"
else
  rm -f "$sudoers_tmp"
  die "generated sudoers file failed validation; left /etc/sudoers.d untouched"
fi

systemctl daemon-reload
systemctl enable --now srrverify.service

log "installed ${VERSION}; service listening on ${HOST}:${PORT}"
log "if auth is wanted: sudo -u ${SVC_USER} ${PREFIX}/venv/bin/python -m app.auth_setup (run from ${PREFIX}/current/backend)"
