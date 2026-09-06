# srrverify — coding standards

The conventions this codebase already follows, written down so reviews have
something to cite. Where the code is inconsistent, the rule below is the target
and the exceptions are debt (see the end).

## General

- No linter or formatter config ships yet. Until one does: 4-space indent and
  `ruff`/`black` defaults for Python, Prettier defaults for TS. Add the config
  before arguing about style in review.
- Keep the dependency list short. The backend deliberately runs on the standard
  library plus FastAPI/uvicorn/httpx; the frontend on React + Vite with no state
  or fetch library. Adding a runtime dependency needs a reason in the PR.
- Comments explain *why*, not *what*. The existing ones are one line, above the
  code they describe (`# Idempotent; also re-run on startup…`). Match that.

## Backend (Python)

### Module layout

- One responsibility per module: `crc` (hashing), `srrdb` (API client + parse),
  `db` (connection + schema), `scanner` (scan orchestration), `auth`
  (auth primitives), `auth_setup` (CLI), `main` (HTTP). `config` holds paths and
  constants read from the environment at import time.
- `main.py` is the exception and is over-broad — see debt. New HTTP surface goes
  in an `APIRouter` module under `routers/`, not appended to `main.py`.

### SQL and the database

- **Every** value in a query is a bound parameter (`?`). No f-string or
  concatenation carries user input. Building a `WHERE` skeleton from literal
  fragments is fine (`library_files`); the values still bind.
- One `sqlite3.Connection` per thread, from `db.get_conn()` (thread-local). Never
  pass a connection between threads.
- Schema is declarative in `db.SCHEMA`, applied with `executescript`, and must
  stay idempotent (`CREATE TABLE IF NOT EXISTS`). `init_db()` is safe to call
  repeatedly.
- Timestamps are `db.utcnow()` (timezone-aware ISO 8601 UTC strings).
- Upserts use `ON CONFLICT(...) DO UPDATE SET col=excluded.col`.

### State and types

- Mutable run state is a `@dataclass` with `field(default_factory=...)` for
  containers and an explicit `as_dict()` for the JSON shape (`ScanState`). New
  structured state follows this — do **not** thread bare dicts with
  `d["key"]` access (`_discover`'s `item` is existing debt).
- Type-hint public functions and dataclass fields. `Optional[X]` for nullable.
- Domain enumerations (file status, scan phase) are module-level constants, not
  bare string literals at each use site. There must be exactly one definition
  per language. See "Shared vocabulary" below.

### Errors

- Scan code catches broad exceptions on purpose, to surface any failure into the
  UI snapshot rather than kill the worker — annotate those with
  `# noqa: BLE001` and a reason, as the existing ones do. Everywhere else, catch
  the specific exception.
- HTTP handlers raise `fastapi.HTTPException(status, detail)`. `detail` is a
  short human sentence.
- Do not put absolute filesystem paths or raw exception reprs in a `detail` or
  in an unauthenticated snapshot (existing violations are debt).

### Naming

- Module-private helpers are `_prefixed` (`_discover`, `_evaluate`,
  `_summary`). Public API is unprefixed.
- Names state the result: `parse_expected_crc`, `fetch_release`,
  `verify_token`. Avoid throwaway SQL aliases that leak into Python
  (`COUNT(*) c` → alias `count`).

### HTTP client

- `httpx.AsyncClient` for outbound calls, one client per batch of lookups, with
  an explicit timeout from `config`. URL path segments built from external data
  go through `urllib.parse.quote(..., safe='')`.

## Frontend (TypeScript / React)

- `tsconfig` is `strict` with `noUnusedLocals`, `noUnusedParameters`,
  `noFallthroughCasesInSwitch`. Keep it that way; fix the code, don't relax the
  config.
- All network access goes through the `api` object in `src/api.ts`. Components
  never call `fetch` directly.
- Response shapes are `interface`s in `api.ts` and must mirror the backend JSON
  exactly, including the status string union.
- The auth token is reached only through the `token` module in `api.ts`
  (`get`/`set`/`clear` over one `localStorage` key). A 401 clears it and
  dispatches the `gamecrc-unauth` event; nothing else handles auth loss.
- Components are function components with hooks. No class components, no Redux /
  Zustand / React Query — local `useState` and `useEffect` only.
- Errors surface as a string in component state and render in an `.error`
  element. `String(e.message || e)` is the idiom.
- Render external strings (file paths, release names, srrdb data) as plain JSX
  text. No `dangerouslySetInnerHTML`.
- Shared pure helpers live in their own module and are imported (not
  copy-pasted — `fmtSize` is current debt).

## Shared vocabulary (backend ↔ frontend ↔ script)

File-status values (`MATCH`, `MISMATCH`, `NOT_FOUND`, `ERROR`, `PENDING`) and
scan-phase values appear in Python, in the `api.ts` union, in `Results.tsx`
tabs, in `App.tsx`'s `phaseLabel`, in `styles.css` class names, and in
`crc32-iso.sh`. They are a single contract. When you change the set:

1. Update the backend constants (the source of truth).
2. Update the `api.ts` union and any switch/label/tab list that enumerates them.
3. Update `styles.css` and `crc32-iso.sh` if the changed value is styled or
   printed there.

Prefer moving display strings to the server (send the label, not just the
phase) so the client stops re-deriving them.

## `crc32-iso.sh`

Frozen. It is the standalone predecessor and duplicates `crc32_file` and
`parse_expected_crc` in embedded Python. Fix bugs in place; do not grow it. New
behavior belongs in the `app` package.

## Commits and repository

- Commit author identity must match the GitHub account that owns the repo
  (`FlightlessWeasel`). Check `git config user.email` before committing.
- No Claude session links or `Claude-Session:` trailers in commit messages or PR
  text.
- Run non-code writing (README, this file, PR descriptions) through an editing
  pass before finalizing.

## Testing

None exists. New non-trivial backend logic (`srrdb` parsing, `scanner`
evaluation, `auth` token/TOTP) should land with `pytest` cases. Set up the test
runner in the PR that adds the first test.

## Known debt (from the `b68cb2f` review)

- Status/phase strings duplicated across ~8 files instead of one constant per
  language.
- `fmtSize` copied verbatim in `App.tsx` and `Results.tsx`.
- `main.py` owns auth, fs browsing, library CRUD, scan control, and static
  serving in one 295-line module.
- `_discover` yields untyped dicts threaded through the scan by `item["key"]`.
- Bearer-header parsing and the `/api/` prefix check are reimplemented several
  times in `main.py`.
- `_summary` computes `SUM(size)` it never returns.
