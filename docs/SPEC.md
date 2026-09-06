# srrverify — specification

Reverse-engineered from the code at `b68cb2f`, then updated after the first
review pass landed its fixes. No spec predated the code; this is the reference
for what the app is meant to do, so future changes can be checked against intent
rather than against the current implementation alone.

**Target platform: headless Linux only.** No Windows or macOS support.

## Purpose

Verify that the files in a local folder match the CRC32 records that
[srrdb.com](https://www.srrdb.com) holds for scene releases. The answer for each
file is one of: matches the record, does not match, no record exists, or the
file could not be read.

`crc32-iso.sh` is the original single-file command-line version of the same
check. It is kept as-is; the web app is the maintained implementation.

## Concepts

- **Library** — a folder the user points the app at. Identified by its resolved
  absolute path (unique). Has a display name (the folder's own name).
- **Scan** — one walk of a library's tree. At most one scan runs at a time
  across the whole process.
- **Release** — the immediate parent folder name of a file, used verbatim as the
  scene release name for the srrdb lookup
  (`https://api.srrdb.com/v1/details/<release>`).
- **File status** — `MATCH`, `MISMATCH`, `NOT_FOUND`, `ERROR`, or `PENDING`
  (stored default before a file is first evaluated).
- **Scan phase** — `starting`, `discovering`, `releases`, `hashing`, `done`,
  `cancelled`, `error`.

## Functional requirements

### Libraries

1. List libraries with, per library: id, path, name, `exists` (path is currently
   a directory), and a summary — file count, per-status counts, and the last
   scan's state and finish time.
2. Add a library by path. The path must currently be an existing directory;
   otherwise reject with a client error. Store the resolved absolute path. Adding
   a path that already exists returns the existing library rather than
   duplicating it.
3. Delete a library. Its file rows are removed with it. Cached `releases` rows
   are **not** removed (they are keyed by release name, not by library).

### Filesystem browser

4. `GET /api/fs/list` with no `path` returns the single root `/`.
5. With a `path`, return that directory's immediate subdirectories (sorted
   case-insensitively) plus its parent, or a 404 if it is not a directory, or a
   403 if it cannot be read. This endpoint only ever exposes directory names, no
   file contents. Error responses do not echo the requested path.

### Scanning

6. Starting a scan requires an existing library whose folder is currently
   present, and no other scan running (409 otherwise).
7. **Discover**: walk the whole tree. Each file yields path, path relative to the
   library root, name, release (parent folder name), size, and mtime in
   nanoseconds. Unreadable entries are skipped silently. Files are processed in
   case-insensitive relative-path order.
8. An empty tree finishes immediately with phase `done` and a "no files" message.
9. **Resolve releases**: for each distinct release name, look up srrdb once.
   - Query `/v1/details/<release>`. A 404 or a response with no file data is a
     miss.
   - On a miss, try `/v1/search/<release>` and accept an exact
     case-insensitive name match, or the sole result if there is exactly one;
     re-fetch details for that name.
   - Cache the outcome (`found`, resolved name, raw JSON) in the `releases`
     table keyed by the original release name.
10. **Hash**: compute CRC32 for each file by streaming it in 1 MiB chunks;
    the digest is 8-char lowercase hex.
11. **Incremental reuse**: if a stored row for the same path has a CRC and its
    size and mtime_ns are unchanged, reuse the stored CRC without re-reading the
    file. Its bytes still count toward progress totals.
12. **Evaluate** each file against its release's cached data:
    - CRC could not be computed → `ERROR`.
    - Release not found, or found but no CRC for this file → `NOT_FOUND`.
    - Expected CRC found: `MATCH` if equal (case-insensitively) to the local
      CRC, else `MISMATCH`.
    - Expected CRC lookup: match srrdb `files` / `archived-files` entries by
      basename (case-insensitive). Fallback for a single-ISO archive: if the
      target ends `.iso` and exactly one archived `.iso` entry has the same byte
      size, use its CRC. Expected CRC must match `^[0-9a-fA-F]{8}$`.
13. **Orphan cleanup**: after a successful pass, delete file rows for paths that
    were not seen in this scan.
14. **Force rescan** (`{"force": true}`): before hashing, delete this library's
    file rows and the `releases` rows for the names in this scan, so every CRC
    is recomputed and every srrdb record re-fetched.
15. Persist progress to the DB periodically during hashing (not only at the end),
    so a crash leaves partial results.
16. Every finished scan (done, cancelled, or errored) writes a row to `scans`
    with state, start/finish time, file counts, and a message.

### Scan progress and control

17. `GET /api/scan/current` returns whether a scan is running and, if state
    exists, a snapshot: library id, phase, a display label for the phase
    (`phase_label`, computed server-side), file counts, byte counts, current
    file, message, and per-status counts.
18. `POST /api/scan/cancel` requests cancellation of the scan that is current at
    that moment. The running scan stops at the next file boundary or chunk
    boundary, commits what it has, and ends in phase `cancelled`. A cancel
    request carries no effect onto a later scan (per-run token).
19. Any unexpected exception during a scan ends it in phase `error`. The snapshot
    message names the exception class only; the full exception is written to the
    server log and to the `scans` row. The process keeps serving.

### Results

20. `GET /api/libraries/{id}/files` returns a page of file rows and the total
    count for the current filter.
    - `status` filters to one status; `ALL` or absent means no status filter.
    - `search` matches a substring (case-insensitive `LIKE`) against relative
      path or release.
    - Ordering: `MISMATCH`, then `ERROR`, then `NOT_FOUND`, then the rest; then by
      relative path.
    - `limit` defaults to 200, clamped to 1–1000; `offset` clamped to ≥ 0.

### Authentication (optional)

21. Auth is **off** until `backend/data/auth.json` exists with `enabled: true`,
    a password hash, and a TOTP secret. Configured by
    `python -m app.auth_setup` (interactive, or headless via
    `GAMECRC_SETUP_USER` / `GAMECRC_SETUP_PASSWORD`, min 8 chars).
22. When on, every `/api/*` route except `health`, `auth/status`, and
    `auth/login` requires `Authorization: Bearer <token>`. `OPTIONS` is exempt.
23. Login requires username + password + current 6-digit TOTP code. Password
    hashing is scrypt; TOTP is RFC 6238 (SHA-1, 6 digits, 30 s, ±1 step window).
    A TOTP counter value cannot be reused (in-process replay guard).
24. Five failed attempts within 300 s block further attempts for the rest of that
    window. Two independent counters are kept: one per source address, one per
    username. A success clears both. The source address is `request.client.host`,
    or the first `X-Forwarded-For` hop when `GAMECRC_TRUST_PROXY` is set.
    A wrong username costs the same time as a wrong password (constant-work
    hash), so response timing does not reveal whether the username exists.
25. Tokens are stateless: base64url JSON `{u, exp}` plus an HMAC-SHA256 signature
    over `server_secret`. TTL 12 h. The only revocation is rotating
    `server_secret`. The browser stores the token in `localStorage`.
26. `auth_setup` sub-commands: `--show` reprints the otpauth URI / QR;
    `--disable` sets `enabled: false` and keeps the secrets.

### Serving

27. `python run.py` serves the API on `GAMECRC_HOST` / `GAMECRC_PORT`.
28. When `frontend/dist/` exists, the backend serves the SPA itself (index plus
    hashed assets, with unknown non-API routes falling back to index.html), so
    no Vite process is needed. In dev, Vite serves the SPA and proxies `/api`
    to the backend.

## Non-functional

- Backend runs on the Python standard library plus FastAPI/uvicorn/httpx. No
  crypto, ORM, or TOTP dependency.
- Frontend is React + Vite + TypeScript, no state-management or data-fetching
  library.
- Large files must stream, never load whole into memory.
- Data lives in one SQLite file at `backend/data/gamecrc.db`; deleting it is a
  full reset. `GAMECRC_DATA_DIR` overrides the location (also moves
  `auth.json`).
- SQLite runs in WAL mode with a 5 s busy timeout; one connection per thread.

## Out of scope

- Extracting or reading inside archives (`.rar`, `.zip`, …). CRC is computed on
  files as they sit on disk.
- Repairing, renaming, moving, or deleting library files. The app is read-only
  against the library.
- More than one user, roles, or per-library access control.
- Hash algorithms other than CRC32.
- Concurrent scans.

## Known gaps / accepted limitations

Fixed after the `b68cb2f` review:

- SPA catch-all now resolves each candidate and refuses anything outside
  `frontend/dist/`; traversal attempts fall through to `index.html`.
- `auth.json` is created `0600` (open flag) and re-`chmod`ed on every write.
- Login throttle keys on source address *and* username; honours
  `X-Forwarded-For` under `GAMECRC_TRUST_PROXY`.
- Constant-work password hash removes the username-enumeration timing signal.
- Scan start takes an `asyncio.Lock`; cancellation is scoped by a per-run token.
- Filesystem-browser and scan errors no longer echo absolute paths or raw
  exception text to the client.

Accepted, by decision:

- Default bind is `0.0.0.0` with auth off. The app is for a trusted LAN and is
  not to be exposed to the internet. Turn auth on before widening exposure.
- No UNC / SMB-path guard on library add — not relevant on the Linux target.

Still open:

- No automated tests.
- `_discover` holds one record per file in memory before hashing; very large
  trees spike RAM.
- Stateless tokens have no revocation short of rotating `server_secret`.
