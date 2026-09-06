import { useCallback, useEffect, useRef, useState } from "react";
import { api, UpdateStatus } from "../api";

type Step = "idle" | "applying" | "waiting" | "done" | "failed";

const stripV = (s: string) => s.replace(/^v/i, "");

export function UpdatePanel() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [step, setStep] = useState<Step>("idle");
  const [note, setNote] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const load = useCallback(async (refresh = false) => {
    try {
      setStatus(await api.updateStatus(refresh));
    } catch {
      /* 401 is handled by the app-level login gate */
    }
  }, []);

  useEffect(() => {
    load();
    return stopPolling;
  }, [load, stopPolling]);

  const apply = async () => {
    if (!status?.latest) return;
    if (
      !confirm(
        `Update to ${status.latest}? The server will restart and this page will reload.`
      )
    ) {
      return;
    }
    setNote(null);
    setStep("applying");
    try {
      await api.applyUpdate();
    } catch (e: any) {
      setStep("failed");
      setNote(String(e.message || e));
      return;
    }

    const target = stripV(status.latest);
    const startedAt = Date.now();
    setStep("waiting");
    pollRef.current = window.setInterval(async () => {
      try {
        const h = await api.health();
        if (stripV(h.version) === target) {
          stopPolling();
          setStep("done");
          window.setTimeout(() => window.location.reload(), 1200);
          return;
        }
      } catch {
        /* server is mid-restart; keep polling */
      }
      if (Date.now() - startedAt > 180_000) {
        stopPolling();
        setStep("failed");
        setNote(
          "The new version never reported in. If it failed its health check the " +
            "server rolls back on its own — check journalctl -u srrverify."
        );
      }
    }, 3000);
  };

  if (!status) return null;

  const version = <span className="version mono">v{stripV(status.current)}</span>;

  if (step === "applying" || step === "waiting") {
    return (
      <div className="update-panel">
        {version}
        <span className="muted small">Updating to {status.latest}…</span>
      </div>
    );
  }
  if (step === "done") {
    return (
      <div className="update-panel">
        {version}
        <span className="muted small">Updated — reloading…</span>
      </div>
    );
  }

  return (
    <div className="update-panel">
      {version}
      {status.can_apply && status.update_available && (
        <>
          <span className="chip update">{status.latest} available</span>
          <button className="primary small" onClick={apply}>
            Install {status.latest}
          </button>
        </>
      )}
      {!status.update_available && !status.error && (
        <button
          className="ghost link small"
          onClick={() => load(true)}
          title="Check for updates now"
        >
          Check for updates
        </button>
      )}
      {status.notes_url && status.update_available && (
        <a
          className="small"
          href={status.notes_url}
          target="_blank"
          rel="noreferrer"
        >
          Release notes
        </a>
      )}
      {step === "failed" && note && <span className="error small">{note}</span>}
      {status.error && <span className="muted small">{status.error}</span>}
    </div>
  );
}
