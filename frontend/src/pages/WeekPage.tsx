import { api } from "../api/client";
import type { ContentType, FeedItem } from "../api/types";
import { SourceAvatar } from "../components/Avatar";
import { ErrorNotice, LoadingRows, Notice, PageHeader } from "../components/common";
import { ItemList } from "../components/ItemRow";
import { useApp } from "../lib/AppContext";
import { TYPE_LABELS, compact } from "../lib/format";
import { hrefFor } from "../lib/router";
import { useAsync } from "../lib/useAsync";

// Order sections by what you'd act on at the weekend: things to build, then read.
const ORDER: ContentType[] = ["project", "research_paper", "news", "blog_post", "discussion"];

function rangeLabel(until: string, days: number): string {
  const end = new Date(until);
  const start = new Date(end.getTime() - days * 86400_000);
  const fmt = (d: Date) => d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return `${fmt(start)} to ${fmt(end)}`;
}

function attention(item: FeedItem): string {
  const stars = Math.max(0, ...item.sightings.map((s) => s.engagement.stars ?? 0));
  if (stars > 0) return `${compact(stars)} stars`;
  const points = item.sightings.reduce((sum, s) => sum + (s.engagement.points ?? 0), 0);
  if (points > 0) return `${compact(points)} points`;
  return `${item.sightings.length} ${item.sightings.length === 1 ? "source" : "sources"}`;
}

/** The week's three biggest stories, as a 2 – 1 – 3 podium. */
function Podium({ items }: { items: FeedItem[] }) {
  const top = [...items].sort((a, b) => b.score - a.score).slice(0, 3);
  if (top.length < 3) return null;
  const order = [top[1], top[0], top[2]];
  const ranks = [2, 1, 3];
  return (
    <section className="podium card beige" aria-labelledby="podium-h">
      <h2 id="podium-h">Top 3 this week</h2>
      <ol className="podium-row">
        {order.map((item, i) => (
          <li key={item.article.id} data-rank={ranks[i]}>
            <a href={hrefFor("week", item.article.id)}>
              <span className="podium-avatar">
                <SourceAvatar sourceId={item.sightings[0]?.source_id ?? ""} size={ranks[i] === 1 ? 64 : 50} />
                <span className="rank" aria-label={`Number ${ranks[i]}`}>
                  {ranks[i]}
                </span>
              </span>
              <span className="podium-title">{item.article.title}</span>
              <span className="podium-meta">{attention(item)}</span>
            </a>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function WeekPage() {
  const { dataVersion } = useApp();
  const digest = useAsync(() => api.digest(7), [dataVersion]);
  const items = digest.data?.items ?? [];

  const groups = new Map<ContentType, FeedItem[]>();
  for (const item of items) {
    const list = groups.get(item.article.content_type) ?? [];
    list.push(item);
    groups.set(item.article.content_type, list);
  }

  return (
    <>
      <PageHeader
        title="This week"
        subtitle={
          digest.data
            ? `${items.length} picks, ${rangeLabel(digest.data.until, digest.data.days)}`
            : "The best of the last 7 days"
        }
      />
      {digest.error ? <ErrorNotice error={digest.error} onRetry={digest.reload} /> : null}
      {digest.loading && !digest.data ? <LoadingRows /> : null}
      {digest.data && items.length === 0 ? (
        <Notice title="No picks yet">
          The weekly list fills up as stories gather points and comments. Check back after a few
          refreshes.
        </Notice>
      ) : null}
      <Podium items={items} />
      {ORDER.filter((t) => groups.has(t)).map((t) => (
        <section key={t} className="group" aria-labelledby={`g-${t}`}>
          <h2 id={`g-${t}`} className="group-title">
            {TYPE_LABELS[t]}
            <span className="pill type" data-type={t}>
              {groups.get(t)!.length}
            </span>
          </h2>
          <ItemList items={groups.get(t)!} tab="week" showType={false} />
        </section>
      ))}
    </>
  );
}
