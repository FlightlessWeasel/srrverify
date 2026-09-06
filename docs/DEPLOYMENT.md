# Deploying and updating srrverify

Target: a headless Linux host on a trusted LAN, run as a systemd service.

## Layout

`scripts/install.sh` lays the app out under a single prefix (`/opt/srrverify`
by default):

```
/opt/srrverify/
  releases/<version>/   an unpacked release tarball (backend + built frontend)
  current -> releases/<version>   symlink the service runs from
  venv/                 shared virtualenv
  bin/update.sh         privileged updater, kept outside the versioned tree
  bin/lib.sh
  data/                 SQLite db, auth.json, update.log — survives updates
```

The systemd unit runs `venv/bin/python run.py` from `current/backend` with
`GAMECRC_DATA_DIR=/opt/srrverify/data`, so data outlives every version swap.

## First install

Needs `curl`, `tar`, `sha256sum`, `python3`, `pip`, `systemctl`, `systemd-run`,
`flock` and `visudo` on the host, plus network access to PyPI (each update
syncs the venv). No git and no Node — the release tarball ships the built
frontend.

```bash
# from a source checkout, or download scripts/ from a release
sudo scripts/install.sh --version v1.4.0 --host 127.0.0.1 --port 8000
```

Options: `--version` (default: the latest published release), `--prefix`,
`--user`, `--host`, `--port`. Re-running repairs the venv, unit file and
sudoers drop-in.

**Exposing beyond localhost.** `--host 0.0.0.0` makes `/api/update/apply`
reachable from the LAN, and with auth off that is unauthenticated remote code
execution as root. Only use it with auth configured first
(`python -m app.auth_setup`, see the README).

The installer writes `/etc/sudoers.d/srrverify` granting the service user
exactly one command — `/opt/srrverify/bin/update.sh --from-app`, no wildcard,
nothing else. It is validated with `visudo -c` in a temp file and only moved
into place if valid. `update.sh` takes no `--prefix`: its prefix is fixed by
its own install path, so the sudo caller cannot point it elsewhere.

## Updating

### From the UI

The header shows the running version. When the release feed has a newer tag an
**Install vX.Y.Z** button appears; it calls `update.sh --from-app` through the
sudoers rule (always targeting the latest release — the UI never pins a
version). The script:

1. re-execs itself into a transient `systemd-run` unit so the app's restart
   can't kill it (if `systemd-run` is missing it refuses the in-app path);
2. takes an exclusive `flock` on `data/update.lock` — one update at a time;
3. downloads the tarball, verifies it against `SHA256SUMS`, extracts it;
4. syncs the venv, repoints `current`, restarts the service;
5. polls `/api/health` for 30s for the new `version`. On failure it re-syncs
   the venv to the previous release, swaps `current` back, and restarts.

The page polls health and reloads once the new version answers. If nothing
reports in within 180s it tells you to check `journalctl -u srrverify` (a
failed update has rolled itself back by then).

The button is hidden on a source checkout (no `bin/update.sh`), which just
shows the version read from the repo-root `VERSION` file (`0.0.0-dev`).

### From the shell

```bash
sudo /opt/srrverify/bin/update.sh                 # to the latest release
sudo /opt/srrverify/bin/update.sh --version v1.5.0
sudo /opt/srrverify/bin/update.sh --rollback      # back to the previous release
sudo /opt/srrverify/bin/update.sh --force         # reinstall the current version
```

Progress is appended to `/opt/srrverify/data/update.log`; the detached run also
logs to the journal (`journalctl -u 'srrverify-update-*'`). Old releases are
pruned to the newest three.

## Releases (CI)

`.github/workflows/release.yml` runs on any `v*` tag push:

1. `npm ci && npm run build` in `frontend/`.
2. Assemble `srrverify-<version>/` — `backend/` (minus `data/` and caches),
   `frontend/dist/`, `scripts/`, `systemd/`, `crc32-iso.sh`, docs, and a
   `VERSION` file holding the tag without its leading `v`.
3. `tar czf srrverify-<version>.tar.gz` plus `SHA256SUMS`.
4. Publish a GitHub Release with both files and auto-generated notes.

Cut a release:

```bash
git tag v1.4.0 && git push origin v1.4.0
```

`.github/workflows/build.yml` builds the frontend, imports the backend, and
shellchecks the scripts on every push to `master` and every PR.

The app finds releases via `GET /repos/<owner>/<repo>/releases/latest`. Point
it at a different repo with `GAMECRC_REPO=owner/repo` (env on the service).
`update.sh` reads `GH_TOKEN` if set, to raise the anonymous rate limit.
