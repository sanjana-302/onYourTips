import { useState } from "react";
import { api } from "../api/client";
import type { Article, KeyPoint, SummaryView } from "../api/types";
import { useAsync } from "../lib/useAsync";


/** AI summary with tappable citations. Each key point cites numbered passages of the
 *  article; tapping [P3] shows that passage, with the quoted words highlighted. */
export function SummaryCard({ article }: { article: Article }) {
  const { data, loading, reload } = useAsync(() => api.summary(article.id), [article.id]);
  const [view, setView] = useState<SummaryView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const s = view ?? data;

  const run = async (force = false) => {
    setBusy(true);
    setError(null);
    try {
      setView(await api.summarize(article.id, force));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't summarize");
    } finally {
      setBusy(false);
      reload();
    }
  };

  return (
    <section className="summary card beige" aria-labelledby="summary-h" aria-busy={busy || loading}>
      <h2 id="summary-h">Summary</h2>
      {s?.status === "ready" ? (
        <ReadySummary s={s} />
      ) : (
        <>
          {article.summary ? (
            <p className="summary-text">{article.summary}</p>
          ) : (
            <p className="summary-text muted">This source didn't include a description.</p>
          )}
          {busy ? (
            <p className="summary-note" role="status">
              <span className="spinner" aria-hidden="true" /> Summarizing with your local model,
              usually 5–30 seconds…
            </p>
          ) : (
            <div className="summary-note summary-cta">
              <span>
                {s?.status === "failed"
                  ? `The AI summary didn't work: ${s.description ?? "unknown reason"}.`
                  : "From the source's own description. No AI summary yet."}
              </span>
              <button className="btn small" onClick={() => void run(s?.status === "failed")}>
                {s?.status === "failed" ? "Try again" : "Summarize with AI"}
              </button>
            </div>
          )}
          {error ? <p className="summary-note warn">{error}</p> : null}
        </>
      )}
    </section>
  );
}

function ReadySummary({ s }: { s: SummaryView }) {
  const passages = new Map(s.passages.map((p) => [p.idx, p.text]));
  return (
    <>
      <p className="summary-tldr">{s.tldr}</p>
      <ol className="key-points">
        {s.key_points.map((kp, i) => (
          <KeyPointItem key={i} kp={kp} passages={passages} />
        ))}
      </ol>
      {s.why_it_matters ? (
        <p className="summary-why">
          <strong>Why it matters.</strong> {s.why_it_matters}
        </p>
      ) : null}
      {s.partial ? (
        <p className="summary-note warn">Long story: this covers its first parts only.</p>
      ) : null}
      <p className="summary-note">{metaLine(s)}</p>
    </>
  );
}

function KeyPointItem({ kp, passages }: { kp: KeyPoint; passages: Map<number, string> }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <li>
      <span>{kp.text}</span>{" "}
      {kp.citations.map((c) => (
        <button
          key={c}
          className="cite-chip"
          aria-expanded={open === c}
          aria-label={`Source passage ${c}`}
          onClick={() => setOpen(open === c ? null : c)}
        >
          P{c}
        </button>
      ))}
      {open !== null && passages.has(open) ? (
        <blockquote className="cite-passage">
          <span className="cite-label">Passage {open}</span>
          <Highlight text={passages.get(open)!} quote={kp.quote} />
        </blockquote>
      ) : null}
    </li>
  );
}

/** Highlights the quoted words inside the passage when they appear there word for word. */
function Highlight({ text, quote }: { text: string; quote: string }) {
  const q = quote.trim().replace(/^["'“]|["'”.]$/g, "");
  const at = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1;
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark>{text.slice(at, at + q.length)}</mark>
      {text.slice(at + q.length)}
    </>
  );
}

function metaLine(s: SummaryView): string {
  const parts = ["AI summary", s.model ?? ""];
  if (s.mode === "map_reduce") parts.push("long story, in parts");
  if (s.input_tokens != null && s.output_tokens != null)
    parts.push(`${fmtK(s.input_tokens)} in / ${fmtK(s.output_tokens)} out`);
  if (s.latency_ms) parts.push(`${(s.latency_ms / 1000).toFixed(1)} s`);
  if (s.cost_usd != null) parts.push(s.cost_usd === 0 ? "$0 (local)" : `$${s.cost_usd.toFixed(4)}`);
  parts.push("tap P-numbers to check");
  return parts.filter(Boolean).join(" · ");
}

function fmtK(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}


