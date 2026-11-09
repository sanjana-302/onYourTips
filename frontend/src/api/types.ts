// Mirrors backend pydantic models (onyourtips.core.models, services, api.schemas).
// Next step: generate these from /openapi.json with openapi-typescript.

export type ContentType = "news" | "research_paper" | "project" | "blog_post" | "discussion";

export type InteractionAction = "open" | "save" | "unsave" | "dismiss" | "undismiss" | "rate";

export interface Engagement {
  points: number | null;
  comments: number | null;
  stars: number | null;
}

export interface Article {
  id: string;
  canonical_url: string;
  url: string;
  title: string;
  summary: string | null;
  author: string | null;
  content_type: ContentType;
  published_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  topics: string[];
  source_tags: string[];
  primary_source_id: string;
  signal: number;
  extra: Record<string, unknown>;
}

export interface Sighting {
  source_id: string;
  external_id: string | null;
  discussion_url: string | null;
  engagement: Engagement;
  seen_at: string;
}

export interface ArticleState {
  opened: boolean;
  saved: boolean;
  dismissed: boolean;
  rating: number | null;
  saved_at: string | null;
  last_interaction_at: string | null;
}

export interface FeedItem {
  article: Article;
  sightings: Sighting[];
  score: number;
  state: ArticleState;
}

export interface FeedResponse {
  items: FeedItem[];
}

export interface DigestResponse {
  days: number;
  until: string;
  items: FeedItem[];
}

export interface TopicInfo {
  slug: string;
  label: string;
  count: number;
}

export interface TopicInterest {
  slug: string;
  label: string;
  score: number;
}

export interface SourceInfo {
  id: string;
  name: string;
  kind: string;
  content_type: ContentType;
  enabled: boolean;
}

export interface SourceHealth {
  source_id: string;
  ok: boolean;
  fetched: number;
  new: number;
  error: string | null;
}

export interface AppStatus {
  articles: number;
  saved: number;
  interactions: number;
  last_run_at: string | null;
  sources: SourceHealth[];
  // Daily refresh (ADR 0005)
  last_refresh_at: string | null;
  last_refresh_trigger: "scheduled" | "manual" | null;
  last_refresh_status: "ok" | "partial" | "failed" | "running" | "skipped" | null;
  next_refresh_at: string | null;
  open_alerts: number;
}

export interface Alert {
  id: number;
  created_at: string;
  kind: "source_failing" | "extraction_gave_up" | "pipeline_failed" | "network_down" | string;
  subject: string;
  message: string;
  details: Record<string, unknown>;
  resolved_at: string | null;
}

export interface IngestStatus {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  totals: Record<string, number> | null;
  failed_sources: string[];
  extracted: number | null;
  error: string | null;
}

export interface KeyPoint {
  text: string;
  citations: number[];
  quote: string;
}

export interface CitedPassage {
  idx: number;
  text: string;
}

export interface SummaryView {
  article_id: string;
  status: "ready" | "missing" | "failed";
  summary_id: number | null;
  tldr: string | null;
  key_points: KeyPoint[];
  why_it_matters: string | null;
  passages: CitedPassage[];
  model: string | null;
  mode: "single" | "map_reduce" | null;
  partial: boolean;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  quote_match_rate: number | null;
  created_at: string | null;
  description: string | null;
}
