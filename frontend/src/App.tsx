import { useCallback, useEffect, useRef, useState } from "react";
import { api, Library, ScanState, token } from "./api";
import { fmtSize } from "./format";
import { DirectoryPicker } from "./components/DirectoryPicker";
import { Login } from "./components/Login";
import { ReleaseList } from "./components/ReleaseList";
import { Results } from "./components/Results";
import { UpdatePanel } from "./components/UpdatePanel";

export default function App() {
  const [gate, setGate] = useState<"loading" | "login" | "ok">("loading");
  const [authEnabled, setAuthEnabled] = useState(false);
  const [username, setUsername] = useState<string | null>(null);
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [picking, setPicking] = useState(false);
  const [scan, setScan] = useState<ScanState | null>(null);
  const [scanRunning, setScanRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [releaseFilter, setReleaseFilter] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const checkAuth = useCallback(async () => {
    try {
      const s = await api.authStatus();
      setAuthEnabled(s.enabled);
      setUsername(s.username ?? null);
      setGate(s.enabled && !s.authenticated ? "login" : "ok");
    } catch {
      setGate("login");
    }
  }, []);

  useEffect(() => {
    checkAuth();
    const drop = () => setGate("login");
    window.addEventListener("gamecrc-unauth", drop);
    return () => window.removeEventListener("gamecrc-unauth", drop);
  }, [checkAuth]);

  const logout = () => {
    token.clear();
    setGate("login");
  };

  const loadLibraries = useCallback(async () => {
    try {
      const libs = await api.libraries();
      setLibraries(libs);
      setActiveId((cur) => cur ?? (libs[0]?.id ?? null));
    } catch {
      /* 401 already routed to the login screen */
    }
  }, []);

  useEffect(() => {
    if (gate === "ok") loadLibraries();
  }, [gate, loadLibraries]);

  // A different library has its own folders; drop any active folder filter.
  useEffect(() => setReleaseFilter(null), [activeId]);

  const poll = useCallback(async () => {
    try {
      const { running, state } = await api.scanCurrent();
      setScan(state);
      setScanRunning(running);
      if (!running) {
        if (pollRef.current) {
          window.clearInterval(pollRef.current);
          pollRef.current = null;
        }
        setRefreshKey((k) => k + 1);
        loadLibraries();
      }
    } catch {
      /* ignore; login gate handles auth loss */
    }
  }, [loadLibraries]);

  useEffect(() => {
    // Resume polling if a scan is already in progress on mount.
    if (gate === "ok") poll();
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [gate, poll]);

  const startPolling = () => {
    if (pollRef.current) return;
    pollRef.current = window.setInterval(poll, 1000);
  };

  const addLibrary = async (path: string) => {
    setPicking(false);
    setError(null);
    try {
      const lib = await api.addLibrary(path);
      await loadLibraries();
      setActiveId(lib.id);
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  const removeLibrary = async (id: number) => {
    if (!confirm("Remove this library and its saved scan results?")) return;
    await api.deleteLibrary(id);
    setActiveId(null);
    await loadLibraries();
  };

  const runScan = async (force: boolean) => {
    if (activeId == null) return;
    setError(null);
    try {
      await api.startScan(activeId, force);
      setScanRunning(true);
      startPolling();
      poll();
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  const active = libraries.find((l) => l.id === activeId) ?? null;
  const pct =
    scan && scan.bytes_total > 0
      ? Math.min(100, (scan.bytes_done / scan.bytes_total) * 100)
      : 0;

  if (gate === "loading") {
    return <div className="app muted">Loading…</div>;
  }
  if (gate === "login") {
    return <Login onDone={checkAuth} />;
  }

  return (
    <div className="app">
      <header>
        <div className="header-row">
          <h1>srrverify</h1>
          <div className="header-actions">
            <UpdatePanel />
            {authEnabled && (
              <button className="ghost" onClick={logout}>
                {username ? `Sign out (${username})` : "Sign out"}
              </button>
            )}
          </div>
        </div>
        <p className="muted">
          Verify library files against srrdb.com CRC records.
        </p>
      </header>

      {error && <div className="error banner">{error}</div>}

      <section className="libraries">
        <div className="lib-row">
          <label>Library</label>
          <select
            value={activeId ?? ""}
            onChange={(e) => setActiveId(Number(e.target.value))}
          >
            {libraries.length === 0 && <option value="">No libraries yet</option>}
            {libraries.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} {l.exists ? "" : "(missing)"} — {l.path}
              </option>
            ))}
          </select>
          <button className="primary" onClick={() => setPicking(true)}>
            + Add library
          </button>
          {active && (
            <button className="ghost" onClick={() => removeLibrary(active.id)}>
              Remove
            </button>
          )}
        </div>

        {active && (
          <div className="lib-actions">
            <button
              className="primary"
              disabled={scanRunning || !active.exists}
              onClick={() => runScan(false)}
            >
              {scanRunning ? "Scanning…" : "Scan"}
            </button>
            <button
              className="ghost"
              disabled={scanRunning || !active.exists}
              onClick={() => runScan(true)}
              title="Recompute every CRC and refresh srrdb lookups"
            >
              Full rescan
            </button>
            {scanRunning && (
              <button className="ghost" onClick={() => api.cancelScan()}>
                Cancel
              </button>
            )}
            <SummaryChips lib={active} />
          </div>
        )}
      </section>

      {scan && (scanRunning || scan.phase !== "done") && (
        <section className="progress-card">
          <div className="progress-line">
            <strong>{scan.phase_label}</strong>
            <span className="muted">
              {scan.files_done}/{scan.files_total} files ·{" "}
              {fmtSize(scan.bytes_done)} / {fmtSize(scan.bytes_total)}
            </span>
          </div>
          <div className="bar">
            <div className="bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <div className="muted small">
            {scan.current_file ?? scan.message ?? ""}
          </div>
          {scan.phase === "error" && (
            <div className="error">{scan.message}</div>
          )}
        </section>
      )}

      {active && (
        <ReleaseList
          libraryId={active.id}
          refreshKey={refreshKey}
          selected={releaseFilter}
          onSelect={(r) =>
            setReleaseFilter((cur) => (cur === r ? null : r))
          }
        />
      )}

      {active && (
        <Results
          libraryId={active.id}
          refreshKey={refreshKey}
          release={releaseFilter}
          onClearRelease={() => setReleaseFilter(null)}
        />
      )}

      {picking && (
        <DirectoryPicker
          onPick={addLibrary}
          onClose={() => setPicking(false)}
        />
      )}
    </div>
  );
}

function SummaryChips({ lib }: { lib: Library }) {
  const c = lib.summary.counts;
  return (
    <div className="chips">
      <span className="chip total">{lib.summary.total} files</span>
      {c.MATCH ? <span className="chip MATCH">{c.MATCH} match</span> : null}
      {c.MISMATCH ? (
        <span className="chip MISMATCH">{c.MISMATCH} mismatch</span>
      ) : null}
      {c.NOT_FOUND ? (
        <span className="chip NOT_FOUND">{c.NOT_FOUND} not found</span>
      ) : null}
      {c.ERROR ? <span className="chip ERROR">{c.ERROR} error</span> : null}
    </div>
  );
}
