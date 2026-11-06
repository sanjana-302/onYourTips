import type { Article, ContentType, Sighting } from "../api/types";

export const TYPE_LABELS: Record<ContentType, string> = {
  news: "News",
  project: "Projects",
  research_paper: "Research",
  blog_post: "Blogs",
  discussion: "Discussions",
};

/** Singular, for a single item's badge. */
export const TYPE_NAMES: Record<ContentType, string> = {
  news: "News",
  project: "Project",
  research_paper: "Paper",
  blog_post: "Blog post",
  discussion: "Discussion",
};

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "short" });

export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const diffSec = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(diffSec);
  if (abs < 60) return "just now";
  if (abs < 3570) return rtf.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diffSec / 3600), "hour");
  if (abs < 86400 * 7) return rtf.format(Math.round(diffSec / 86400), "day");
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function articleTime(a: Article): string {
  return timeAgo(a.published_at ?? a.first_seen_at);
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Readable source name, falling back to the source id. */
export function sourceName(id: string, names: Map<string, string>): string {
  return names.get(id) ?? id;
}

/** Short name for compact rows: "Hacker News · Front page" -> "Hacker News". */
export function shortSourceName(id: string, names: Map<string, string>): string {
  return sourceName(id, names).split(" · ")[0];
}

export function joinNames(names: string[]): string {
  const unique = [...new Set(names)];
  if (unique.length <= 2) return unique.join(" and ");
  return `${unique.slice(0, 2).join(", ")} +${unique.length - 2}`;
}

export function engagementText(s: Sighting): string | null {
  const e = s.engagement;
  const parts: string[] = [];
  if (e.points != null) parts.push(`${compact(e.points)} points`);
  if (e.stars != null) parts.push(`${compact(e.stars)} stars`);
  if (e.comments != null) parts.push(`${compact(e.comments)} comments`);
  return parts.length ? parts.join(", ") : null;
}

export function compact(n: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

/** Heat level 1–4 relative to the hottest item in view. */
export function heatLevel(score: number, max: number): 1 | 2 | 3 | 4 {
  if (max <= 0) return 1;
  const r = score / max;
  return r > 0.66 ? 4 : r > 0.4 ? 3 : r > 0.2 ? 2 : 1;
}

/** "Updated 6 hr. ago, next at 1:00 AM" */
export function refreshLine(last: string | null, next: string | null): string {
  const parts: string[] = [];
  parts.push(last ? `Updated ${timeAgo(last)}` : "Not refreshed yet");
  if (next) {
    const t = new Date(next).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    parts.push(`next at ${t}`);
  }
  return parts.join(", ");
}

export function todayLabel(d = new Date()): string {
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}
