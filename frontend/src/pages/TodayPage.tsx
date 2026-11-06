import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ContentType } from "../api/types";
import {
  Chips,
  ErrorNotice,
  LoadingRows,
  Notice,
  OptionRow,
  PageHeader,
  SearchBar,
  Sheet,
} from "../components/common";
import { GettingStarted } from "../components/GettingStarted";
import { Icon } from "../components/Icon";
import { ItemList } from "../components/ItemRow";
import { RefreshButton } from "../components/RefreshButton";
import { useApp } from "../lib/AppContext";
import { TYPE_LABELS, refreshLine, todayLabel } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const TYPES = Object.keys(TYPE_LABELS) as ContentType[];
const RANGES = [
  { hours: 24, label: "Last 24 hours" },
  { hours: 72, label: "Last 3 days" },
  { hours: 168, label: "Last 7 days" },
];

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function TodayPage() {
  const { dataVersion } = useApp();
  const [types, setTypes] = useState<ContentType[]>([]);
  const [topics, setTopics] = useState<string[]>([]);
  const [hours, setHours] = useState(24);
  const [search, setSearch] = useState("");
  const [sheetOpen, setSheetOpen] = useState(false);
  const q = useDebounced(search.trim(), 300);

  const feed = useAsync(
    () => api.feed({ hours, type: types, topic: topics, q, limit: 60 }),
    [hours, types.join(), topics.join(), q, dataVersion],
  );
  const status = useAsync(() => api.status(), [dataVersion]);

  const filterCount = topics.length + (hours !== 24 ? 1 : 0);
  const filtered = types.length > 0 || filterCount > 0 || q !== "";
  const items = feed.data?.items ?? [];
  const clearAll = () => {
    setTypes([]);
    setTopics([]);
    setHours(24);
    setSearch("");
  };

  return (
    <>
      <PageHeader
        title="Today"
        subtitle={todayLabel()}
        detail={
          status.data
            ? refreshLine(status.data.last_refresh_at, status.data.next_refresh_at)
            : undefined
        }
        action={<RefreshButton />}
      />
      <GettingStarted status={status.data} />
      <SearchBar value={search} onChange={setSearch} placeholder="Search stories" />
      <Chips
        label="Content type"
        options={TYPES.map((t) => ({ value: t, label: TYPE_LABELS[t] }))}
        selected={types}
        onToggle={(t) => setTypes((prev) => toggle(prev, t))}
        trailing={
          <button
            className="chip filter-chip"
            aria-pressed={filterCount > 0}
            aria-haspopup="dialog"
            onClick={() => setSheetOpen(true)}
          >
            <Icon name="sliders" size={16} />
            Filters
            {filterCount ? <span className="count-badge">{filterCount}</span> : null}
          </button>
        }
      />

      {feed.error ? <ErrorNotice error={feed.error} onRetry={feed.reload} /> : null}
      {feed.loading && !feed.data ? <LoadingRows /> : null}
      {feed.data && items.length === 0 ? (
        filtered ? (
          <Notice
            title="Nothing matches"
            action={
              <button className="btn" onClick={clearAll}>
                Clear search and filters
              </button>
            }
          >
            {q ? `No stories mention “${q}” in this time range.` : "Try fewer filters."}
          </Notice>
        ) : (status.data?.articles ?? 0) > 0 ? (
          <Notice
            title="Nothing new in the last day"
            action={
              <button className="btn" onClick={() => setHours(72)}>
                Show the last 3 days
              </button>
            }
          >
            Tap Refresh for the latest, or look further back.
          </Notice>
        ) : null
      ) : null}
      {items.length > 0 ? <ItemList items={items} tab="today" /> : null}
      {items.length > 0 && hours === 24 ? (
        <button className="btn more" onClick={() => setHours(72)}>
          Show the last 3 days
        </button>
      ) : null}

      {sheetOpen ? (
        <FilterSheet
          hours={hours}
          topics={topics}
          onClose={() => setSheetOpen(false)}
          onApply={(h, t) => {
            setHours(h);
            setTopics(t);
            setSheetOpen(false);
          }}
        />
      ) : null}
    </>
  );
}

function FilterSheet({
  hours,
  topics,
  onClose,
  onApply,
}: {
  hours: number;
  topics: string[];
  onClose: () => void;
  onApply: (hours: number, topics: string[]) => void;
}) {
  // Edit a draft; nothing changes until "Show stories" is tapped.
  const [h, setH] = useState(hours);
  const [t, setT] = useState(topics);
  const opts = useAsync(() => api.topics(Math.ceil(h / 24)), [h]);
  const available = (opts.data ?? []).filter((x) => x.count > 0 || t.includes(x.slug));

  return (
    <Sheet
      title="Filters"
      onClose={onClose}
      onReset={() => {
        setH(24);
        setT([]);
      }}
      footer={
        <button className="btn confirm" onClick={() => onApply(h, t)}>
          Show stories
        </button>
      }
    >
      <h3 className="sheet-section">Time range</h3>
      <div className="options">
        {RANGES.map((r) => (
          <OptionRow
            key={r.hours}
            label={r.label}
            checked={h === r.hours}
            onToggle={() => setH(r.hours)}
          />
        ))}
      </div>
      <h3 className="sheet-section">Topics</h3>
      {opts.data && available.length === 0 ? (
        <p className="muted small">No tagged stories in this time range yet.</p>
      ) : null}
      <div className="options">
        {available.map((x) => (
          <OptionRow
            key={x.slug}
            label={x.label}
            detail={x.count ? `${x.count}` : undefined}
            checked={t.includes(x.slug)}
            onToggle={() => setT((prev) => toggle(prev, x.slug))}
          />
        ))}
      </div>
    </Sheet>
  );
}
