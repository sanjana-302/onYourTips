"""Core data models.

Flow of data:

    Source connector  ->  RawItem            (what a source returned, minimally parsed)
    Pipeline          ->  Article + Sighting (entity + "who saw it, with what engagement")
    Database          ->  FeedItem           (article + all sightings + rank score, for the API/app)

An ``Article`` is unique per canonical URL. The same article seen on Hacker News, Reddit and an
RSS feed is stored once, with three ``Sighting`` rows. Multiple sightings boost its rank.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SourceKind(str, Enum):
    RSS = "rss"
    HACKERNEWS = "hackernews"
    ARXIV = "arxiv"
    HF_PAPERS = "hf_papers"
    GITHUB = "github"
    REDDIT = "reddit"
    DEVTO = "devto"


class ContentType(str, Enum):
    NEWS = "news"
    RESEARCH_PAPER = "research_paper"
    PROJECT = "project"
    BLOG_POST = "blog_post"
    DISCUSSION = "discussion"


# When two sources disagree on the type of the same article, the more specific one wins.
CONTENT_TYPE_PRIORITY: dict[ContentType, int] = {
    ContentType.RESEARCH_PAPER: 4,
    ContentType.PROJECT: 3,
    ContentType.BLOG_POST: 2,
    ContentType.NEWS: 1,
    ContentType.DISCUSSION: 0,
}


# to add/remove sources use sources.yaml
class SourceConfig(BaseModel):
    """One entry from ``config/sources.yaml``."""

    id: str
    name: str
    kind: SourceKind
    content_type: ContentType
    enabled: bool = True
    limit: int = Field(default=30, ge=1, le=200)
    # Multiplier applied to this source's contribution to an article's signal.
    weight: float = Field(default=1.0, ge=0)
    params: dict[str, Any] = Field(default_factory=dict)


class Engagement(BaseModel):
    points: int | None = None  # HN points, Reddit score, dev.to reactions, HF upvotes
    comments: int | None = None
    stars: int | None = None  # GitHub stars


class RawItem(BaseModel):
    """What a connector returns. Text may still contain HTML; URL is not canonicalized."""

    source_id: str
    url: str
    title: str
    external_id: str | None = None
    summary: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    discussion_url: str | None = None
    engagement: Engagement = Field(default_factory=Engagement)
    tags: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class Sighting(BaseModel):
    """A single source observing an article."""

    source_id: str
    external_id: str | None = None
    discussion_url: str | None = None
    engagement: Engagement = Field(default_factory=Engagement)
    seen_at: datetime


class Article(BaseModel):
    id: str  # sha256(canonical_url)[:16]
    canonical_url: str
    url: str
    title: str
    title_fp: str | None = None  # fingerprint of normalized title, for cross-URL dedupe
    summary: str | None = None
    author: str | None = None
    content_type: ContentType
    published_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    topics: list[str] = Field(default_factory=list)  # from our taxonomy (deterministic rules)
    source_tags: list[str] = Field(default_factory=list)  # tags as given by the sources
    primary_source_id: str
    signal: float = 0.0  # engagement-based, time-independent; see ranking.scoring
    extra: dict[str, Any] = Field(default_factory=dict)


class InteractionType(str, Enum):
    OPEN = "open"
    SAVE = "save"
    UNSAVE = "unsave"
    DISMISS = "dismiss"
    UNDISMISS = "undismiss"
    RATE = "rate"  # value = 1..5


class Interaction(BaseModel):
    """Append-only event log of what *you* did with an article — training data for the
    future recommender. Never updated in place; current state is derived by folding events."""

    id: int | None = None
    article_id: str
    action: InteractionType
    value: int | None = None
    note: str | None = None
    created_at: datetime


class ArticleState(BaseModel):
    """Your current relationship with an article, derived from the interaction log."""

    opened: bool = False
    saved: bool = False
    dismissed: bool = False
    rating: int | None = None
    saved_at: datetime | None = None
    last_interaction_at: datetime | None = None

    @property
    def interest_weight(self) -> float:
        """How strongly this article signals your interest (used by the interest profile)."""
        w = 3.0 * self.saved + 1.0 * self.opened - 2.0 * self.dismissed
        if self.rating is not None:
            w += self.rating - 3  # 5 -> +2, 1 -> -2
        return w


class FeedItem(BaseModel):
    article: Article
    sightings: list[Sighting]
    score: float  # signal, optionally with recency decay applied at query time
    state: ArticleState = Field(default_factory=ArticleState)


class UpsertOutcome(str, Enum):
    NEW = "new"  # article never seen before
    MERGED = "merged"  # existing article, seen by a new source
    UPDATED = "updated"  # existing article + source, engagement refreshed


class SourceRunResult(BaseModel):
    source_id: str
    ok: bool
    fetched: int = 0
    new: int = 0
    merged: int = 0
    updated: int = 0
    skipped: int = 0
    error: str | None = None
    duration_s: float = 0.0


class RunReport(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime
    results: list[SourceRunResult]

    @property
    def totals(self) -> dict[str, int]:
        keys = ("fetched", "new", "merged", "updated", "skipped")
        return {k: sum(getattr(r, k) for r in self.results) for k in keys}


# ----------------------------------------------------------------------------- full text (ADR 0002)


class ContentStatus(str, Enum):
    OK = "ok"  # full text extracted
    EXCERPT = "excerpt"  # only the feed's description was usable
    SKIPPED = "skipped"  # not an article page (PDF, video, blocked by robots.txt)
    FAILED = "failed"  # nothing usable


class Passage(BaseModel):
    """A numbered chunk of an article's text. Citations point to `idx`."""

    idx: int
    text: str
    char_start: int
    char_end: int


class ArticleContent(BaseModel):
    id: int | None = None
    article_id: str
    content_hash: str | None = None
    extractor: str  # trafilatura | html-fallback | github-readme | feed
    extractor_version: str
    source_url: str
    text: str | None = None
    word_count: int = 0
    status: ContentStatus
    error: str | None = None
    retryable: bool = False  # transient failure (timeout, 5xx, rate limit): retry next night
    counts_as_attempt: bool = True  # False: failed while the network was down (doesn't count)
    fetched_at: datetime

    @property
    def usable(self) -> bool:
        return self.status in (ContentStatus.OK, ContentStatus.EXCERPT) and bool(self.text)


# --------------------------------------------------------------- pipeline + alerts (ADR 0005)


class Alert(BaseModel):
    id: int | None = None
    created_at: datetime
    # source_failing | extraction_gave_up | pipeline_failed | network_down | llm_unavailable
    # | summaries_unfinished
    kind: str
    subject: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    resolved_at: datetime | None = None


class PipelineRun(BaseModel):
    id: int | None = None
    run_id: str
    trigger: str  # scheduled | manual
    started_at: datetime
    finished_at: datetime | None = None
    status: str  # running | ok | partial | failed
    steps: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
