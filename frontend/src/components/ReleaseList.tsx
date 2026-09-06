import { useEffect, useState } from "react";
import { api, ReleaseRow } from "../api";

/** Public srrdb.com page for a release known to exist there. */
function srrdbUrl(r: ReleaseRow): string {
  const name = r.resolved_name || r.release;
  return `https://www.srrdb.com/release/details/${encodeURIComponent(name)}`;
}

export function ReleaseList({
  libraryId,
  refreshKey,
  selected,
  onSelect,
}: {
  libraryId: number;
  refreshKey: number;
  selected: string | null;
  onSelect: (release: string) => void;
}) {
  const [rows, setRows] = useState<ReleaseRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .releases(libraryId)
      .then(setRows)
      .catch((e) => setError(String(e.message || e)))
      .finally(() => setLoading(false));
  }, [libraryId, refreshKey]);

  if (error) return <div className="error">{error}</div>;
  if (!loading && rows.length === 0) return null;

  return (
    <div className="release-list">
      <h2>Game folders</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Folder</th>
              <th>Match</th>
              <th className="num">Files</th>
              <th>srrdb</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const barClass = r.match_pct === 100 ? "full" : "partial";
              return (
                <tr
                  key={r.release}
                  className={
                    "release-row" +
                    (selected === r.release ? " selected" : "")
                  }
                  onClick={() => onSelect(r.release)}
                >
                  <td className="path" title={r.release}>
                    {r.release}
                  </td>
                  <td>
                    <div className="match-cell">
                      <div className="bar mini">
                        <div
                          className={`bar-fill ${barClass}`}
                          style={{ width: `${r.match_pct}%` }}
                        />
                      </div>
                      <span className="muted small">
                        {r.match_pct}% · {r.counts.MATCH ?? 0}/{r.total}
                      </span>
                    </div>
                  </td>
                  <td className="num">{r.total}</td>
                  <td>
                    {r.found ? (
                      <a
                        href={srrdbUrl(r)}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                      >
                        view release ↗
                      </a>
                    ) : (
                      <span className="muted">not on srrdb</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
