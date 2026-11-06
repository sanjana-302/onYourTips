import { useState, type CSSProperties } from "react";
import { api } from "../api/client";
import { SourceAvatar } from "../components/Avatar";
import { ErrorNotice, Notice, PageHeader } from "../components/common";
import { RefreshButton } from "../components/RefreshButton";
import { useApp } from "../lib/AppContext";
import { compact, sourceName, timeAgo } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const BAR_COLORS = ["var(--green)", "var(--lav)", "var(--peach)", "var(--orange)"];

export function YouPage({ active }: { active: boolean }) {
  const { dataVersion, sourceNames } = useApp();
  const interests = useAsync(() => api.interests(7), [active, dataVersion]);
  const status = useAsync(() => api.status(), [active, dataVersion]);

  const positive = (interests.data ?? []).filter((t) => t.score > 0);
  const negative = (interests.data ?? []).filter((t) => t.score < 0);
  const top = Math.max(1, ...positive.map((t) => t.score));
  const failing = (status.data?.sources ?? []).filter((s) => !s.ok);

  return (
    <>
      <PageHeader title="You" subtitle="What you've been into this week" />

      {status.data ? (
        <dl className="facts">
          <div className="card beige">
            <dt>Stories</dt>
            <dd>{compact(status.data.articles)}</dd>
          </div>
          <div className="card beige">
            <dt>Saved</dt>
            <dd>{status.data.saved}</dd>
          </div>
          <div className="card beige">
            <dt>Refreshed</dt>
            <dd>{status.data.last_refresh_at ? timeAgo(status.data.last_refresh_at) : "Never"}</dd>
          </div>
        </dl>
      ) : null}

      {status.data && status.data.open_alerts > 0 ? (
        <AlertsPanel onChange={status.reload} version={dataVersion} />
      ) : null}

      <section className="panel card" aria-labelledby="int-h">
        <h2 id="int-h">Your topics</h2>
        {interests.error ? (
          <ErrorNotice error={interests.error} onRetry={interests.reload} />
        ) : null}
        {interests.data && positive.length === 0 ? (
          <p className="muted">
            Save, open and rate stories and your favourite topics will show up here.
          </p>
        ) : null}
        <ul className="bars">
          {positive.map((t, i) => (
            <li key={t.slug}>
              <span className="bar-label">{t.label}</span>
              <span className="bar-track">
                <span
                  className="bar"
                  style={
                    {
                      "--w": `${(t.score / top) * 100}%`,
                      "--c": BAR_COLORS[i % BAR_COLORS.length],
                    } as CSSProperties
                  }
                />
              </span>
            </li>
          ))}
        </ul>
        {negative.length ? (
          <p className="muted small less">Showing less of: {negative.map((t) => t.label).join(", ")}</p>
        ) : null}
      </section>

      <section className="panel card" aria-labelledby="src-h">
        <div className="panel-head">
          <h2 id="src-h">Sources</h2>
          <RefreshButton label="Refresh now" />
        </div>
        {status.data ? (
          <p className="muted small schedule-note">
            Refreshes automatically every day
            {status.data.next_refresh_at
              ? ` at ${new Date(status.data.next_refresh_at).toLocaleTimeString(undefined, {
                  hour: "numeric",
                  minute: "2-digit",
                })}`
              : ""}
            .{" "}
            {status.data.last_refresh_trigger === "manual"
              ? "The last refresh was started manually."
              : null}
          </p>
        ) : null}
        {status.error ? <ErrorNotice error={status.error} onRetry={status.reload} /> : null}
        {status.data ? (
          failing.length ? (
            <>
              <p className="warn">
                {failing.length} {failing.length === 1 ? "source" : "sources"} didn't respond last
                time
              </p>
              <ul className="failing">
                {failing.map((s) => (
                  <li key={s.source_id}>
                    <SourceAvatar sourceId={s.source_id} size={32} />
                    <span>
                      <strong>{sourceName(s.source_id, sourceNames)}</strong>
                      <span className="muted small">{s.error}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : status.data.sources.length ? (
            <p className="ok">All {status.data.sources.length} sources worked last time.</p>
          ) : (
            <Notice title="No refresh yet">Tap Refresh now to collect your first stories.</Notice>
          )
        ) : null}
      </section>
    </>
  );
}

const ALERT_TITLES: Record<string, string> = {
  source_failing: "Source keeps failing",
  extraction_gave_up: "Pages given up on",
  pipeline_failed: "Refresh failed",
  network_down: "No internet during refresh",
};

function AlertsPanel({ onChange, version }: { onChange: () => void; version: number }) {
  const { showToast } = useApp();
  const alerts = useAsync(() => api.alerts(), [version]);
  const [busy, setBusy] = useState<number | null>(null);

  const resolve = async (id: number) => {
    setBusy(id);
    try {
      await api.resolveAlert(id);
      alerts.reload();
      onChange();
      showToast({ message: "Marked as handled" });
    } catch (e) {
      showToast({ message: e instanceof Error ? e.message : "Couldn't update the alert." });
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel card alerts" aria-labelledby="alerts-h">
      <h2 id="alerts-h">Needs attention</h2>
      <ul className="alert-list">
        {(alerts.data ?? []).map((a) => (
          <li key={a.id}>
            <span className="alert-dot" aria-hidden="true" />
            <div className="alert-body">
              <strong>{ALERT_TITLES[a.kind] ?? a.kind}</strong>
              <span className="alert-msg">{a.message}</span>
              <span className="muted small">{timeAgo(a.created_at)}</span>
            </div>
            <button className="btn small" disabled={busy === a.id} onClick={() => resolve(a.id)}>
              Mark handled
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
