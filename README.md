# srrverify

Web app that scans a library folder, computes CRC32 for every file, and checks
each one against the CRC records on [srrdb.com](https://www.srrdb.com). Results
are cached in a local SQLite database so a folder is only hashed once.

Built around `crc32-iso.sh`, which does the same check for `.iso` files from the
command line.

**Target: headless Linux.** The backend paths, the folder picker (single `/`
root), and `crc32-iso.sh` assume Linux. It is meant to run on a LAN host, not to
be exposed to the internet.

## How it works

- A **library** is a folder you point the app at.
- A **scan** walks the whole tree. Files are grouped by their immediate parent
  folder name, which is used as the scene release name for the srrdb lookup
  (`https://api.srrdb.com/v1/details/<release>`).
- For each file: CRC32 is computed by streaming, matched to the release's file
  list by name (with a size fallback for single-ISO archives), and compared.
- Status per file: `MATCH`, `MISMATCH`, `NOT_FOUND` (no srrdb record), `ERROR`
  (file could not be read).
- On the next scan, files whose size and mtime are unchanged keep their stored
  CRC. srrdb responses are cached too. **Full rescan** ignores both caches.

## Layout

```
backend/   FastAPI + SQLite. Scanning, CRC, srrdb client. HTTP routes in app/routers/.
frontend/  React + Vite. Library manager, folder picker, progress, results table.
```

## Run it

### Install on headless Linux

Copy and paste this command on the Linux host. It downloads the installer and
installs the latest published release as a systemd service under `/opt/srrverify`.

```bash
tmp="$(mktemp -d)" && trap 'rm -rf "$tmp"' EXIT && curl -fsSL https://raw.githubusercontent.com/FlightlessWeasel/srrverify/v0.0.4/scripts/install.sh -o "$tmp/install.sh" && curl -fsSL https://raw.githubusercontent.com/FlightlessWeasel/srrverify/v0.0.4/scripts/lib.sh -o "$tmp/lib.sh" && sudo bash "$tmp/install.sh"
```

The installer needs `curl`, `tar`, `sha256sum`, `python3`, `systemctl`, and
`visudo`. See [Deployment and updates](docs/DEPLOYMENT.md) for options and
updates.

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py                  # serves http://0.0.0.0:8000 (all interfaces)
```

`run.py` binds `0.0.0.0:8000`. Override with `GAMECRC_HOST` (e.g. `127.0.0.1`)
and `GAMECRC_PORT`.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                    # http://localhost:5173, proxies /api to :8000
```

Open http://localhost:5173.

### Single-process option

`npm run build` writes `frontend/dist/`. When that exists, the backend serves the
UI itself on port 8000 — no Vite process needed. This is the mode to use when
reaching the app from another machine.

## Authentication (optional, with MFA)

Auth is **off by default** — the app serves with no login. Because `run.py`
listens on all interfaces, turn it on before exposing the app beyond a trusted
LAN. The filesystem browser reveals directory names across the machine, so this
matters.

Enable it (run from `backend/`):

```bash
python -m app.auth_setup
```

It prompts for a username and password, generates a TOTP secret, and prints a QR
code plus a manual key. Scan it with Google Authenticator, Authy, 1Password, etc.
Restart the server afterwards.

- **Login** requires username + password + the current 6-digit code. A code
  cannot be reused; five failures — counted per source address *and* per
  username — trigger a 5-minute lockout. Set `GAMECRC_TRUST_PROXY=1` if the app
  runs behind a reverse proxy so the throttle keys on `X-Forwarded-For`.
- Sessions are stateless HMAC-signed bearer tokens, valid 12 hours, held in the
  browser's `localStorage`.
- Config lives in `backend/data/auth.json` (written `0600`), separate from the
  main database.

Other commands:

```bash
python -m app.auth_setup --show      # reprint the QR / otpauth URI
python -m app.auth_setup --disable   # back to no login
```

Headless setup (no prompts): set `GAMECRC_SETUP_USER` and
`GAMECRC_SETUP_PASSWORD` before running `python -m app.auth_setup`.

Password hashing is `scrypt`; TOTP is RFC 6238 (SHA-1, 6 digits, 30s). Both are
implemented on the standard library — no crypto dependency.

## Data

SQLite file at `backend/data/gamecrc.db`. Delete it to reset. Override the
location with the `GAMECRC_DATA_DIR` environment variable (also moves
`auth.json`).

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Liveness check (never requires auth) |
| GET | `/api/auth/status` | Whether auth is enabled and the token is valid |
| POST | `/api/auth/login` | `{"username", "password", "code"}` → `{"token"}` |
| GET | `/api/fs/list?path=` | List subdirectories (the `/` root when `path` omitted) |
| GET | `/api/libraries` | Libraries with scan summaries |
| POST | `/api/libraries` | Add a library (`{"path": "..."}`) |
| DELETE | `/api/libraries/{id}` | Remove a library and its results |
| GET | `/api/libraries/{id}/files` | Results, filterable by `status` and `search` |
| POST | `/api/libraries/{id}/scan` | Start a scan (`{"force": bool}`) |
| GET | `/api/scan/current` | Progress of the running scan |
| POST | `/api/scan/cancel` | Cancel it |

When auth is enabled, every `/api` route except `health`, `auth/status`, and
`auth/login` needs an `Authorization: Bearer <token>` header. One scan runs at a
time.

## Deployment and updates

For a persistent install, run it as a systemd service with
[`scripts/install.sh`](scripts/install.sh) — it unpacks a GitHub Release
tarball under `/opt/srrverify`, wires up the unit, and grants the service user
one passwordless command to update itself. After that the header shows the
running version and offers an **Install vX.Y.Z** button when a newer release
exists; `sudo /opt/srrverify/bin/update.sh` does the same from the shell, with
`--rollback` to go back. CI publishes a Release on every `v*` tag push.

Full details: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

See [docs/SPEC.md](docs/SPEC.md) for the behavioural contract and
[docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md) for conventions.
