# Shared helpers for install.sh / update.sh. Source this, do not execute it.
# shellcheck shell=bash
#
# Expects in the environment:
#   PREFIX          install root (e.g. /opt/srrverify)
#   GAMECRC_REPO    optional  owner/repo   (default FlightlessWeasel/srrverify)
#   GH_TOKEN        optional  GitHub token (raises the anonymous rate limit)

REPO="${GAMECRC_REPO:-FlightlessWeasel/srrverify}"
API="https://api.github.com/repos/${REPO}"
DL="https://github.com/${REPO}/releases/download"

log() { printf '[srrverify] %s\n' "$*" >&2; }
die() { printf '[srrverify] error: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"; }

# curl, with an auth header when GH_TOKEN is set.
gh_curl() {
  local url="$1"
  shift
  if [ -n "${GH_TOKEN:-}" ]; then
    curl -fsSL -H "Authorization: Bearer ${GH_TOKEN}" "$@" "$url"
  else
    curl -fsSL "$@" "$url"
  fi
}

# Echo the newest release tag, e.g. "v1.4.0".
latest_tag() {
  gh_curl "${API}/releases/latest" |
    grep -m1 '"tag_name"' |
    sed -E 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
}

# download_release <tag> <destdir>
#   Fetch srrverify-<ver>.tar.gz + SHA256SUMS, verify the checksum, extract into
#   <destdir> (stripping the top-level srrverify-<ver>/ directory).
download_release() {
  local tag="$1" dest="$2" ver tmp
  ver="${tag#v}"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN

  log "downloading ${tag}"
  gh_curl "${DL}/${tag}/srrverify-${ver}.tar.gz" -o "${tmp}/srrverify-${ver}.tar.gz"
  gh_curl "${DL}/${tag}/SHA256SUMS" -o "${tmp}/SHA256SUMS"

  (
    cd "$tmp" &&
      grep " srrverify-${ver}.tar.gz\$" SHA256SUMS | sha256sum -c -
  ) || die "checksum mismatch for srrverify-${ver}.tar.gz"

  mkdir -p "$dest"
  tar -xzf "${tmp}/srrverify-${ver}.tar.gz" -C "$dest" --strip-components=1
  log "extracted ${tag} to ${dest}"
}

# ensure_venv <requirements.txt>
#   Create <PREFIX>/venv if absent, then (re)install requirements.
ensure_venv() {
  local reqs="$1" venv_error venv_status py_version venv_pkg os_id
  if [ ! -x "${PREFIX}/venv/bin/python" ]; then
    log "creating virtualenv at ${PREFIX}/venv"
    venv_error="$(mktemp)"
    if python3 -m venv "${PREFIX}/venv" >"$venv_error" 2>&1; then
      rm -f "$venv_error"
    else
      venv_status=$?
      os_id="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
      if { [ "$os_id" = debian ] || [ "$os_id" = ubuntu ]; } \
        && command -v apt-get >/dev/null 2>&1 \
        && { grep -Fqi 'ensurepip is not available' "$venv_error" \
          || grep -Fqi 'No module named ensurepip' "$venv_error"; }; then
        cat "$venv_error" >&2
        rm -f "$venv_error"
        py_version="$(python3 -c 'import sys; print("%s.%s" % sys.version_info[:2])')"
        venv_pkg="python${py_version}-venv"
        # The failed venv attempt may have left a partial environment behind.
        # This branch is reached only when no valid environment existed above.
        rm -rf -- "${PREFIX}/venv"
        log "installing ${venv_pkg}"
        if ! apt-get install -y "$venv_pkg"; then
          venv_pkg=python3-venv
          log "falling back to ${venv_pkg}"
          apt-get install -y "$venv_pkg"
        fi
        python3 -m venv "${PREFIX}/venv"
      else
        cat "$venv_error" >&2
        rm -f "$venv_error"
        return "$venv_status"
      fi
    fi
  fi
  "${PREFIX}/venv/bin/pip" install --quiet --upgrade pip
  "${PREFIX}/venv/bin/pip" install --quiet -r "$reqs"
}

# swap_current <release_dir>  — atomically repoint <PREFIX>/current.
swap_current() {
  ln -sfn "$1" "${PREFIX}/current.tmp"
  mv -Tf "${PREFIX}/current.tmp" "${PREFIX}/current"
}

# prune_releases <keep>  — remove all but the newest <keep> release dirs, never
#   the one <PREFIX>/current points at.
prune_releases() {
  local keep="$1" live
  live="$(readlink -f "${PREFIX}/current" 2>/dev/null || true)"
  # shellcheck disable=SC2012  # release dir names are tags; sorting by mtime needs ls
  ls -1dt "${PREFIX}"/releases/*/ 2>/dev/null | tail -n +"$((keep + 1))" |
    while read -r d; do
      [ "$(readlink -f "$d")" = "$live" ] && continue
      log "pruning old release $(basename "$d")"
      rm -rf "$d"
    done
}
