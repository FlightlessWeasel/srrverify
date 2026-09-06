import { useEffect, useRef, useState } from "react";
import { api, FileRow, FileStatus } from "../api";
import { fmtSize } from "../format";

const STATUS_TABS: ("ALL" | FileStatus)[] = [
  "ALL",
  "MISMATCH",
  "NOT_FOUND",
  "ERROR",
  "MATCH",
];
const PAGE = 200;

export function Results({
  libraryId,
  refreshKey,
  release,
  onClearRelease,
}: {
  libraryId: number;
  refreshKey: number;
  release?: string | null;
  onClearRelease?: () => void;
}) {
  const [status, setStatus] = useState<string>("ALL");
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ total: number; items: FileRow[] }>({
    total: 0,
    items: [],
  });
  const [loading, setLoading] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => setPage(0), [status, debounced, libraryId, release]);

  // Picking a folder up in the list scrolls its file rows into view.
  useEffect(() => {
    if (release) rootRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [release]);

  useEffect(() => {
    setLoading(true);
    api
      .files(libraryId, {
        status,
        search: debounced,
        release: release ?? undefined,
        limit: PAGE,
        offset: page * PAGE,
      })
      .then(setData)
      .finally(() => setLoading(false));
  }, [libraryId, status, debounced, release, page, refreshKey]);

  const pages = Math.max(1, Math.ceil(data.total / PAGE));

  return (
    <div className="results" ref={rootRef}>
      {release && (
        <div className="filter-note">
          <span>
            Folder: <strong>{release}</strong>
          </span>
          <button className="link" onClick={onClearRelease}>
            clear
          </button>
        </div>
      )}
      <div className="results-controls">
        <div className="tabs">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              className={status === s ? "tab active" : "tab"}
              onClick={() => setStatus(s)}
            >
              {s.replace("_", " ")}
            </button>
          ))}
        </div>
        <input
          className="search"
          placeholder="Filter by path or release…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>File</th>
              <th>Release</th>
              <th className="num">Size</th>
              <th>Local CRC</th>
              <th>Expected CRC</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((r) => (
              <tr key={r.id}>
                <td>
                  <span className={`badge ${r.status}`}>
                    {r.status.replace("_", " ")}
                  </span>
                </td>
                <td className="path" title={r.rel_path}>
                  {r.rel_path}
                </td>
                <td className="release">{r.release}</td>
                <td className="num">{fmtSize(r.size)}</td>
                <td className="mono">{r.crc32 ?? "—"}</td>
                <td className="mono">{r.expected_crc ?? "—"}</td>
              </tr>
            ))}
            {!loading && data.items.length === 0 && (
              <tr>
                <td colSpan={6} className="muted center">
                  Nothing here yet. Run a scan.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="pager">
        <span className="muted">
          {data.total} file{data.total === 1 ? "" : "s"}
        </span>
        {pages > 1 && (
          <div className="pager-buttons">
            <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              ‹ Prev
            </button>
            <span>
              Page {page + 1} / {pages}
            </span>
            <button
              disabled={page >= pages - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              Next ›
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
