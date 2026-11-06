import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "../api/client";
import type { ArticleState, FeedItem, InteractionAction } from "../api/types";

interface Toast {
  id: number;
  message: string;
  actionLabel?: string;
  onAction?: () => void;
}

interface AppContextValue {
  sourceNames: Map<string, string>;
  sourceKinds: Map<string, string>;
  topicLabels: Map<string, string>;
  /** Latest known state per article, so a change in one view shows everywhere. */
  stateOf: (item: FeedItem) => ArticleState;
  act: (item: FeedItem, action: InteractionAction, value?: number) => Promise<void>;
  toast: Toast | null;
  showToast: (t: Omit<Toast, "id">) => void;
  dismissToast: () => void;
  /** Bumped after a background ingest finishes, so lists refetch. */
  dataVersion: number;
  bumpData: () => void;
}

const Ctx = createContext<AppContextValue | null>(null);

// What the UI should show immediately, before the server confirms.
function optimistic(state: ArticleState, action: InteractionAction, value?: number): ArticleState {
  switch (action) {
    case "save":
      return { ...state, saved: true };
    case "unsave":
      return { ...state, saved: false };
    case "dismiss":
      return { ...state, dismissed: true };
    case "undismiss":
      return { ...state, dismissed: false };
    case "rate":
      return { ...state, rating: value ?? state.rating };
    case "open":
      return { ...state, opened: true };
  }
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [sourceNames, setSourceNames] = useState<Map<string, string>>(new Map());
  const [sourceKinds, setSourceKinds] = useState<Map<string, string>>(new Map());
  const [topicLabels, setTopicLabels] = useState<Map<string, string>>(new Map());
  const [overrides, setOverrides] = useState<Map<string, ArticleState>>(new Map());
  const [toast, setToast] = useState<Toast | null>(null);
  const [dataVersion, setDataVersion] = useState(0);
  const toastTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    api
      .sources()
      .then((s) => {
        setSourceNames(new Map(s.map((x) => [x.id, x.name])));
        setSourceKinds(new Map(s.map((x) => [x.id, x.kind])));
      })
      .catch(() => {});
    api
      .topics(30)
      .then((t) => setTopicLabels(new Map(t.map((x) => [x.slug, x.label]))))
      .catch(() => {});
  }, []);

  const dismissToast = useCallback(() => {
    window.clearTimeout(toastTimer.current);
    setToast(null);
  }, []);

  const showToast = useCallback((t: Omit<Toast, "id">) => {
    window.clearTimeout(toastTimer.current);
    setToast({ ...t, id: Date.now() });
    toastTimer.current = window.setTimeout(() => setToast(null), 4500);
  }, []);

  const stateOf = useCallback(
    (item: FeedItem) => overrides.get(item.article.id) ?? item.state,
    [overrides],
  );

  const setStateFor = useCallback((id: string, st: ArticleState) => {
    setOverrides((prev) => new Map(prev).set(id, st));
  }, []);

  const act = useCallback(
    async (item: FeedItem, action: InteractionAction, value?: number) => {
      const id = item.article.id;
      const before = overrides.get(id) ?? item.state;
      setStateFor(id, optimistic(before, action, value));
      try {
        setStateFor(id, await api.interact(id, action, value));
      } catch (e) {
        setStateFor(id, before);
        showToast({ message: e instanceof Error ? e.message : "That didn't save. Try again." });
      }
    },
    [overrides, setStateFor, showToast],
  );

  const bumpData = useCallback(() => setDataVersion((v) => v + 1), []);

  const value = useMemo(
    () => ({
      sourceNames,
      sourceKinds,
      topicLabels,
      stateOf,
      act,
      toast,
      showToast,
      dismissToast,
      dataVersion,
      bumpData,
    }),
    [sourceNames, sourceKinds, topicLabels, stateOf, act, toast, showToast, dismissToast, dataVersion, bumpData],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp must be used inside <AppProvider>");
  return v;
}
