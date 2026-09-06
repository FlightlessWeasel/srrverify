export interface Library {
  id: number;
  path: string;
  name: string;
  created_at: string;
  exists: boolean;
  summary: LibrarySummary;
}

export interface LibrarySummary {
  total: number;
  counts: Record<string, number>;
  last_scan: { finished_at: string; state: string } | null;
}

export interface FileRow {
  id: number;
  rel_path: string;
  name: string;
  release: string;
  size: number;
  crc32: string | null;
  expected_crc: string | null;
  status: "MATCH" | "MISMATCH" | "NOT_FOUND" | "ERROR" | "PENDING";
  scanned_at: string | null;
}

export interface ScanState {
  library_id: number;
  phase: string;
  files_total: number;
  files_done: number;
  bytes_total: number;
  bytes_done: number;
  current_file: string | null;
  message: string | null;
  counts: Record<string, number>;
}

export interface DirListing {
  path: string | null;
  parent: string | null;
  entries: { name: string; path: string }[];
}

export interface AuthStatus {
  enabled: boolean;
  authenticated: boolean;
  username?: string | null;
}

const TOKEN_KEY = "gamecrc_token";

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY) || "",
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

/** Raised on 401 so the UI can drop to the login screen. */
export class Unauthorized extends Error {}

async function request<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  const t = token.get();
  if (t) headers.set("Authorization", `Bearer ${t}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(path, { ...init, headers });

  if (res.status === 401) {
    token.clear();
    window.dispatchEvent(new Event("gamecrc-unauth"));
    throw new Unauthorized("Session expired. Sign in again.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  authStatus: () => request<AuthStatus>("/api/auth/status"),
  login: (username: string, password: string, code: string) =>
    request<{ token: string; username: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password, code }),
    }),

  listDir: (path?: string) =>
    request<DirListing>(
      `/api/fs/list${path ? `?path=${encodeURIComponent(path)}` : ""}`
    ),
  libraries: () => request<Library[]>("/api/libraries"),
  addLibrary: (path: string) =>
    request<Library>("/api/libraries", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  deleteLibrary: (id: number) =>
    request<void>(`/api/libraries/${id}`, { method: "DELETE" }),
  files: (
    id: number,
    opts: { status?: string; search?: string; limit?: number; offset?: number }
  ) => {
    const p = new URLSearchParams();
    if (opts.status) p.set("status", opts.status);
    if (opts.search) p.set("search", opts.search);
    if (opts.limit) p.set("limit", String(opts.limit));
    if (opts.offset) p.set("offset", String(opts.offset));
    return request<{ total: number; items: FileRow[] }>(
      `/api/libraries/${id}/files?${p}`
    );
  },
  startScan: (id: number, force: boolean) =>
    request<{ started: boolean }>(`/api/libraries/${id}/scan`, {
      method: "POST",
      body: JSON.stringify({ force }),
    }),
  scanCurrent: () =>
    request<{ running: boolean; state: ScanState | null }>("/api/scan/current"),
  cancelScan: () =>
    request<unknown>("/api/scan/cancel", { method: "POST" }),
};
