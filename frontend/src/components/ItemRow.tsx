import type { FeedItem } from "../api/types";
import { useApp } from "../lib/AppContext";
import { TYPE_NAMES, articleTime, heatLevel, shortSourceName } from "../lib/format";
import { hrefFor, type Tab } from "../lib/router";
import { AvatarStack } from "./Avatar";
import { Icon } from "./Icon";

export function StoryCard({
  item,
  maxScore,
  tab,
  showType = true,
}: {
  item: FeedItem;
  maxScore: number;
  tab: Tab;
  showType?: boolean;
}) {
  const { stateOf, act, showToast, sourceNames, topicLabels } = useApp();
  const state = stateOf(item);
  const a = item.article;
  const hot = heatLevel(item.score, maxScore) === 4 && item.sightings.length > 0;
  const sourceIds = item.sightings.map((s) => s.source_id);
  const first = sourceIds[0] ? shortSourceName(sourceIds[0], sourceNames) : "";
  const others = new Set(sourceIds).size - 1;
  const topics = a.topics.slice(0, 2).map((t) => topicLabels.get(t) ?? t);
  const href = hrefFor(tab, a.id);

  if (state.dismissed) return null;

  const hide = () => {
    void act(item, "dismiss");
    showToast({
      message: "Hidden. You'll see less like this.",
      actionLabel: "Undo",
      onAction: () => void act(item, "undismiss"),
    });
  };

  return (
    <li className="story" data-saved={state.saved || undefined}>
      <a className="story-body" href={href}>
        <div className="story-head">
          <AvatarStack sourceIds={sourceIds} />
          <div className="story-source">
            <span className="story-source-name">
              {first}
              {others > 0 ? <span className="muted"> +{others} more</span> : null}
            </span>
            <span className="story-time">{articleTime(a)}</span>
          </div>
          {hot ? (
            <span className="badge-hot">
              <Icon name="flame" size={15} filled strokeWidth={0} /> Hot
            </span>
          ) : null}
        </div>
        <h3 className="story-title">{a.title}</h3>
        {a.content_type === "project" && a.summary ? (
          <p className="story-desc">{a.summary}</p>
        ) : null}
        {showType || topics.length ? (
          <div className="pills">
            {showType ? (
              <span className="pill type" data-type={a.content_type}>
                {TYPE_NAMES[a.content_type]}
              </span>
            ) : null}
            {topics.map((t) => (
              <span key={t} className="pill">
                {t}
              </span>
            ))}
          </div>
        ) : null}
      </a>
      <div className="story-foot">
        <a className="foot-cta" href={href}>
          <span className="plus-dot" aria-hidden="true">
            <Icon name="plus" size={14} strokeWidth={2.6} />
          </span>
          Read summary
        </a>
        <button
          className="foot-btn"
          aria-pressed={state.saved}
          onClick={() => void act(item, state.saved ? "unsave" : "save")}
        >
          <Icon name="bookmark" size={18} filled={state.saved} />
          {state.saved ? "Saved" : "Save"}
        </button>
        <button className="foot-btn" onClick={hide} aria-label="Hide this story">
          <Icon name="eyeOff" size={18} />
          Hide
        </button>
      </div>
    </li>
  );
}

export function ItemList({
  items,
  tab,
  showType = true,
}: {
  items: FeedItem[];
  tab: Tab;
  showType?: boolean;
}) {
  const maxScore = Math.max(0, ...items.map((i) => i.score));
  return (
    <ul className="stories">
      {items.map((item) => (
        <StoryCard
          key={item.article.id}
          item={item}
          maxScore={maxScore}
          tab={tab}
          showType={showType}
        />
      ))}
    </ul>
  );
}
