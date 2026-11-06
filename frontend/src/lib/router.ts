import { useEffect, useState } from "react";

// Hash routing: works when the built app is served as static files by FastAPI,
// with no server-side fallback needed.
export type Tab = "today" | "week" | "saved" | "you";
export type Route = { tab: Tab; articleId: string | null };

const TABS: Tab[] = ["today", "week", "saved", "you"];

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  // "#/a/<id>" opens an article over the last tab; "#/week/a/<id>" keeps the tab explicit.
  let tab: Tab = "today";
  if (parts[0] && (TABS as string[]).includes(parts[0])) tab = parts.shift() as Tab;
  const articleId = parts[0] === "a" && parts[1] ? decodeURIComponent(parts[1]) : null;
  return { tab, articleId };
}

export function hrefFor(tab: Tab, articleId?: string): string {
  const base = tab === "today" ? "#/" : `#/${tab}`;
  if (!articleId) return base;
  return `${base === "#/" ? "#" : base}/a/${encodeURIComponent(articleId)}`;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function navigate(href: string): void {
  window.location.hash = href.replace(/^#/, "");
}
