import { useEffect, useRef } from "react";
import { api } from "../api/client";
import type { FeedItem } from "../api/types";
import { useApp } from "../lib/AppContext";
import { TYPE_NAMES, articleTime, engagementText, hostOf, sourceName } from "../lib/format";
import { hrefFor, navigate, type Tab } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { SourceAvatar } from "./Avatar";
import { SummaryCard } from "./SummaryCard";
import { ErrorNotice } from "./common";
import { Icon } from "./Icon";

const RATING_WORDS = ["", "Not useful", "Meh", "Okay", "Useful", "Loved it"];

export function ArticleView({ id, tab }: { id: string; tab: Tab }) {
  const { data, error, loading, reload } = useAsync(() => api.article(id), [id]);
  const panelRef = useRef<HTMLDivElement>(null);

  const close = () => {
    if (window.history.length > 1) window.history.back();
    else navigate(hrefFor(tab));
  };

  useEffect(() => {
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  return (
    <div className="article-layer" role="dialog" aria-modal="true" aria-label="Story">
      <div className="article-panel" ref={panelRef} tabIndex={-1}>
        <div className="article-topbar">
          <button className="back-btn" onClick={close}>
            <Icon name="back" size={20} strokeWidth={2.2} />
            Back
          </button>
          <span className="handle" aria-hidden="true" />
          <span className="topbar-spacer" />
        </div>
        {error ? <ErrorNotice error={error} onRetry={reload} /> : null}
        {loading && !data ? <div className="article-loading" aria-busy="true" /> : null}
        {data ? <ArticleBody item={data} /> : null}
      </div>
    </div>
  );
}

function ArticleBody({ item }: { item: FeedItem }) {
  const { stateOf, act, showToast, sourceNames, topicLabels } = useApp();
  const state = stateOf(item);
  const a = item.article;

  const hide = () => {
    void act(item, "dismiss");
    showToast({
      message: "Hidden. You'll see less like this.",
      actionLabel: "Undo",
      onAction: () => void act(item, "undismiss"),
    });
    window.history.back();
  };

  return (
    <>
      <article className="article">
        <div className="article-meta">
          <span className="pill type" data-type={a.content_type}>
            {TYPE_NAMES[a.content_type]}
          </span>
          <span className="muted">
            {hostOf(a.url)}, {articleTime(a)}
          </span>
        </div>
        <h1 className="article-title">{a.title}</h1>
        {a.author ? <p className="article-author">by {a.author}</p> : null}

        <SummaryCard article={a} />

        {a.topics.length ? (
          <div className="pills article-topics" aria-label="Topics">
            {a.topics.map((t) => (
              <span key={t} className="pill">
                {topicLabels.get(t) ?? t}
              </span>
            ))}
          </div>
        ) : null}

        <section className="where" aria-labelledby="where-h">
          <h2 id="where-h">Where people are talking</h2>
          <ul>
            {item.sightings.map((s) => {
              const href = s.discussion_url ?? a.url;
              const stats = engagementText(s);
              return (
                <li key={s.source_id}>
                  <a className="where-card card" href={href} target="_blank" rel="noreferrer">
                    <SourceAvatar sourceId={s.source_id} size={40} />
                    <span className="where-text">
                      <span className="where-name">{sourceName(s.source_id, sourceNames)}</span>
                      {stats ? <span className="where-stats">{stats}</span> : null}
                    </span>
                    <span className="where-go">
                      {s.discussion_url ? <Icon name="chat" size={16} /> : <Icon name="external" size={16} />}
                      {s.discussion_url ? "Discussion" : "Open"}
                    </span>
                  </a>
                </li>
              );
            })}
          </ul>
        </section>

        <section className="rate card" aria-labelledby="rate-h">
          <h2 id="rate-h">How useful was this?</h2>
          <div className="stars" role="radiogroup" aria-label="Rating">
            {[1, 2, 3, 4, 5].map((n) => (
              <button
                key={n}
                role="radio"
                aria-checked={state.rating === n}
                aria-label={`${n} of 5: ${RATING_WORDS[n]}`}
                data-on={state.rating != null && n <= state.rating ? true : undefined}
                onClick={() => void act(item, "rate", n)}
              >
                <Icon name="star" size={30} filled={state.rating != null && n <= state.rating} />
              </button>
            ))}
          </div>
          <p className="rate-word">
            {state.rating ? RATING_WORDS[state.rating] : "Tap a star. Ratings tune your feed."}
          </p>
        </section>
      </article>

      <div className="action-bar">
        <button
          className="btn action"
          aria-pressed={state.saved}
          onClick={() => void act(item, state.saved ? "unsave" : "save")}
        >
          <Icon name="bookmark" size={18} filled={state.saved} />
          {state.saved ? "Saved" : "Save"}
        </button>
        <button className="btn action" onClick={hide}>
          <Icon name="eyeOff" size={18} />
          Hide
        </button>
        <a
          className="btn confirm"
          href={a.url}
          target="_blank"
          rel="noreferrer"
          onClick={() => void act(item, "open")}
        >
          Read article
          <Icon name="arrow" size={18} strokeWidth={2.2} />
        </a>
      </div>
    </>
  );
}
