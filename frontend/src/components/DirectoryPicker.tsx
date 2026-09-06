import { useEffect, useState } from "react";
import { api, DirListing } from "../api";

export function DirectoryPicker({
  onPick,
  onClose,
}: {
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [listing, setListing] = useState<DirListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = (path?: string) => {
    setLoading(true);
    setError(null);
    api
      .listDir(path)
      .then(setListing)
      .catch((e) => setError(String(e.message || e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => load(undefined), []);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Choose a library folder</h3>
          <button className="ghost" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="crumbs">
          <button className="link" onClick={() => load(undefined)}>
            Drives
          </button>
          {listing?.path && <span className="crumb-current">{listing.path}</span>}
        </div>

        {error && <div className="error">{error}</div>}
        {loading && <div className="muted">Loading…</div>}

        <ul className="dirlist">
          {listing?.parent && (
            <li>
              <button className="dir" onClick={() => load(listing.parent!)}>
                📁 ..
              </button>
            </li>
          )}
          {listing?.entries.map((e) => (
            <li key={e.path}>
              <button className="dir" onClick={() => load(e.path)}>
                📁 {e.name}
              </button>
            </li>
          ))}
          {listing && listing.entries.length === 0 && (
            <li className="muted">No subfolders here.</li>
          )}
        </ul>

        <div className="modal-foot">
          <span className="muted">{listing?.path ?? "Select a drive"}</span>
          <button
            className="primary"
            disabled={!listing?.path}
            onClick={() => listing?.path && onPick(listing.path)}
          >
            Use this folder
          </button>
        </div>
      </div>
    </div>
  );
}
