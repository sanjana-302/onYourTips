import type {
  Alert,
  AppStatus,
  ArticleState,
  ContentType,
  DigestResponse,
  FeedItem,
  FeedResponse,
  IngestStatus,
  InteractionAction,
  SourceInfo,
  SummaryView,
  TopicInfo,
  TopicInterest,
} from "./types";

type Params = Record<string, string | number | string[] | undefined | null>;

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function query(params: Params = {}): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => qs.append(key, v));
    else qs.append(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Can't reach OnYourTips. Is `oyt serve` running on your computer?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  feed: (p: { hours?: number; topic?: string[]; type?: ContentType[]; q?: string; limit?: number }) =>
    request<FeedResponse>(`/feed${query(p)}`),
  digest: (days = 7) => request<DigestResponse>(`/digest${query({ days })}`),
  article: (id: string) => request<FeedItem>(`/articles/${encodeURIComponent(id)}`),
  interact: (id: string, action: InteractionAction, value?: number) =>
    request<ArticleState>(`/articles/${encodeURIComponent(id)}/interactions`, {
      method: "POST",
      body: JSON.stringify(value === undefined ? { action } : { action, value }),
    }),
  saved: () => request<FeedResponse>("/saved"),
  topics: (days = 7) => request<TopicInfo[]>(`/topics${query({ days })}`),
  interests: (days = 7) => request<TopicInterest[]>(`/interests${query({ days })}`),
  sources: () => request<SourceInfo[]>("/sources"),
  status: () => request<AppStatus>("/status"),
  startIngest: () => request<{ started: boolean }>("/ingest", { method: "POST" }),
  ingestStatus: () => request<IngestStatus>("/ingest"),
  summary: (id: string) => request<SummaryView>(`/articles/${encodeURIComponent(id)}/summary`),
  summarize: (id: string, force = false) =>
    request<SummaryView>(`/articles/${encodeURIComponent(id)}/summarize${query({ force: force ? "true" : undefined })}`, {
      method: "POST",
    }),
  alerts: () => request<Alert[]>("/alerts"),
  resolveAlert: (id: number) =>
    request<{ resolved: number }>(`/alerts/${id}/resolve`, { method: "POST" }),
};
