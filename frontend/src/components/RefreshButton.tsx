import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useApp } from "../lib/AppContext";
import { Icon } from "./Icon";

/**
 * Starts a background ingest on the server and polls until it finishes.
 * `variant="pill"` shows a labelled button; `"cta"` is the dark button used in the
 * getting-started card.
 */
export function RefreshButton({
  variant = "pill",
  label = "Refresh",
}: {
  variant?: "pill" | "cta";
  label?: string;
}) {
  const { showToast, bumpData } = useApp();
  const [running, setRunning] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const poll = () => {
    timer.current = window.setTimeout(async () => {
      try {
        const st = await api.ingestStatus();
        if (st.running) return poll();
        setRunning(false);
        bumpData();
        const fresh = st.totals ? st.totals.new + st.totals.merged : 0;
        const failed = st.failed_sources.length;
        showToast({
          message: st.error
            ? `Refresh failed: ${st.error}`
            : `${fresh} new ${fresh === 1 ? "story" : "stories"}` +
              (st.extracted ? `, full text for ${st.extracted}` : "") +
              (failed ? `, ${failed} ${failed === 1 ? "source" : "sources"} failed` : ""),
        });
      } catch (e) {
        setRunning(false);
        showToast({ message: e instanceof Error ? e.message : "Lost track of the refresh." });
      }
    }, 2000);
  };

  useEffect(() => {
    // Pick up a run already in progress (started by the schedule or another device).
    api
      .ingestStatus()
      .then((st) => {
        if (st.running) {
          setRunning(true);
          poll();
        }
      })
      .catch(() => {});
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = async () => {
    setRunning(true);
    try {
      await api.startIngest();
      poll();
    } catch (e) {
      setRunning(false);
      showToast({ message: e instanceof Error ? e.message : "Couldn't start a refresh." });
    }
  };

  return (
    <button
      className={variant === "cta" ? "btn dark small" : "btn pill-refresh"}
      onClick={start}
      disabled={running}
      data-spinning={running || undefined}
    >
      <Icon name="refresh" size={17} strokeWidth={2.2} />
      {running ? "Refreshing…" : label}
    </button>
  );
}
