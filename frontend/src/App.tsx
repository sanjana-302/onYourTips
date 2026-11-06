import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { ArticleView } from "./components/ArticleView";
import { TabBar, ToastHost } from "./components/common";
import { AppProvider } from "./lib/AppContext";
import { useRoute, type Tab } from "./lib/router";
import { setFlag } from "./lib/storage";
import { SavedPage } from "./pages/SavedPage";
import { TodayPage } from "./pages/TodayPage";
import { WeekPage } from "./pages/WeekPage";
import { YouPage } from "./pages/YouPage";

function Shell() {
  const { tab, articleId } = useRoute();

  useEffect(() => {
    document.body.classList.toggle("locked", articleId !== null);
  }, [articleId]);

  useEffect(() => {
    if (tab === "week") setFlag("visited-week"); // completes a getting-started step
  }, [tab]);

  // Each tab keeps its own scroll position; a tab you haven't scrolled opens at the top.
  const scrolls = useRef<Record<string, number>>({});
  const lastTab = useRef<Tab>(tab);
  useEffect(() => {
    const onScroll = () => {
      if (articleId === null) scrolls.current[lastTab.current] = window.scrollY;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [articleId]);
  useLayoutEffect(() => {
    if (lastTab.current !== tab) {
      lastTab.current = tab;
      window.scrollTo(0, scrolls.current[tab] ?? 0);
    }
  }, [tab]);

  // Tabs stay mounted (hidden when inactive) so filters and loaded lists survive switching.
  const page = (t: Tab, node: ReactNode) => (
    <main className="page" hidden={tab !== t} aria-hidden={articleId !== null || undefined}>
      {node}
    </main>
  );

  return (
    <div className="app">
      {page("today", <TodayPage />)}
      {page("week", <WeekPage />)}
      {page("saved", <SavedPage active={tab === "saved"} />)}
      {page("you", <YouPage active={tab === "you"} />)}
      {articleId ? <ArticleView id={articleId} tab={tab} /> : null}
      <TabBar active={tab} />
      <ToastHost />
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
