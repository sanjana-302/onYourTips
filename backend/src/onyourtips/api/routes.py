"""HTTP routes. Each handler parses input, calls one service, and returns a response model."""

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from onyourtips import services
from onyourtips.api.deps import get_db, get_ingest_runner, get_settings, get_tagger
from onyourtips.api.schemas import DigestResponse, FeedResponse, IngestStarted, InteractionIn
from onyourtips.core.models import Alert, ArticleState, ContentType, FeedItem
from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow
from onyourtips.ingestion.tagging import Tagger
from onyourtips.services import IngestRunner, nightly
from onyourtips.services.llm import CostSummary, LLMStatus, cost_summary, llm_status
from onyourtips.services.summaries import SummaryView, get_summary_view, summarize_article
from onyourtips.services.tokens import TokenReport, token_report
from onyourtips.storage import Database

router = APIRouter(prefix="/api")

DB = Annotated[Database, Depends(get_db)]
Cfg = Annotated[Settings, Depends(get_settings)]
Tags = Annotated[Tagger, Depends(get_tagger)]
Runner = Annotated[IngestRunner, Depends(get_ingest_runner)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/feed", response_model=FeedResponse)
def feed(
    db: DB,
    settings: Cfg,
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    topic: Annotated[list[str] | None, Query()] = None,
    type: Annotated[list[ContentType] | None, Query()] = None,  # noqa: A002 - public param name
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
) -> FeedResponse:
    items = services.get_feed(
        db, settings, hours=hours, topics=topic, content_types=type, query=q, limit=limit
    )
    return FeedResponse(items=items)


@router.get("/digest", response_model=DigestResponse)
def digest(
    db: DB,
    settings: Cfg,
    days: Annotated[int, Query(ge=1, le=60)] = 7,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    topic: Annotated[list[str] | None, Query()] = None,
) -> DigestResponse:
    until = utcnow()
    items = services.build_digest(db, settings, days=days, until=until, limit=limit, topics=topic)
    return DigestResponse(days=days, until=until, items=items)


@router.get("/articles/{article_id}", response_model=FeedItem)
def article(article_id: str, db: DB) -> FeedItem:
    item = services.get_article(db, article_id)
    if item is None:
        raise HTTPException(status_code=404, detail="article not found")
    return item


@router.post("/articles/{article_id}/interactions", response_model=ArticleState)
def interact(article_id: str, body: InteractionIn, db: DB) -> ArticleState:
    try:
        return services.record_interaction(
            db, article_id, body.action, value=body.value, note=body.note
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/saved", response_model=FeedResponse)
def saved(db: DB, days: Annotated[int | None, Query(ge=1, le=3650)] = None) -> FeedResponse:
    return FeedResponse(items=services.list_saved(db, days))


@router.get("/topics", response_model=list[services.TopicInfo])
def topics(db: DB, tagger: Tags, days: Annotated[int, Query(ge=1, le=60)] = 7):
    return services.list_topics(db, tagger, days)


@router.get("/interests", response_model=list[services.TopicInterest])
def interests(db: DB, tagger: Tags, days: Annotated[int, Query(ge=1, le=365)] = 7):
    return services.interests(db, tagger, days)


@router.get("/articles/{article_id}/content", response_model=services.ContentView)
def article_content(article_id: str, db: DB) -> services.ContentView:
    """Full text + numbered passages, if the article has been extracted."""
    view = services.get_content(db, article_id)
    if view is None:
        raise HTTPException(status_code=404, detail="not extracted yet; POST to /extract")
    return view


@router.get("/articles/{article_id}/summary", response_model=SummaryView)
def article_summary(article_id: str, settings: Cfg) -> SummaryView:
    """The story's AI summary with citations: ready, missing, or failed (with the reason)."""
    with Database(settings.db_path) as db:
        try:
            return get_summary_view(settings, db, article_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="article not found") from exc


_summarize_lock = threading.Lock()


@router.post("/articles/{article_id}/summarize", response_model=SummaryView)
def summarize(article_id: str, settings: Cfg, force: bool = False) -> SummaryView:
    """Summarize now (about 5–30 s with a local model). One at a time: the model runs one
    request at a time anyway. Runs in FastAPI's thread pool, with its own connection."""
    if not _summarize_lock.acquire(timeout=120):
        raise HTTPException(status_code=409, detail="another summary is running; try again")
    try:
        with Database(settings.db_path) as db:
            try:
                out = summarize_article(settings, db, article_id, force=force)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="article not found") from exc
            return get_summary_view(settings, db, out.article_id)
    finally:
        _summarize_lock.release()


@router.post("/articles/{article_id}/extract", response_model=services.ContentView)
async def extract_article(
    article_id: str, db: DB, settings: Cfg, force: bool = False
) -> services.ContentView:
    """Fetch and store the full text of one article now (returns the cached copy if present)."""
    try:
        full_id = db.resolve_id(article_id)
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail="article not found") from None
    await services.extract_articles(db, settings, [full_id], force=force)
    view = services.get_content(db, full_id)
    assert view is not None
    return view


@router.get("/llm/status", response_model=LLMStatus)
def llm_status_route(settings: Cfg) -> LLMStatus:
    return llm_status(settings)


@router.get("/costs", response_model=CostSummary)
def costs(
    db: DB,
    days: Annotated[int, Query(ge=1, le=365)] = 7,
    by: Annotated[str, Query(pattern="^(model|purpose|day)$")] = "model",
) -> CostSummary:
    return cost_summary(db, days=days, by=by)


@router.get("/tokens", response_model=TokenReport)
def tokens(settings: Cfg, db: DB, days: Annotated[int, Query(ge=1, le=90)] = 7) -> TokenReport:
    """How many tokens a summary of each article would use (cost calibration)."""
    return token_report(settings, db, days=days)


@router.get("/sources", response_model=list[services.SourceInfo])
def sources(settings: Cfg):
    return services.list_sources(settings)


@router.get("/status", response_model=services.AppStatus)
def status(db: DB, settings: Cfg) -> services.AppStatus:
    return services.app_status(db, settings)


@router.get("/alerts", response_model=list[Alert])
def alerts(db: DB, include_resolved: bool = False) -> list[Alert]:
    """Things a developer should check: failing sources, pages given up on, failed runs."""
    return nightly.list_alerts(db, include_resolved=include_resolved)


@router.post("/alerts/{alert_id}/resolve", response_model=dict)
def resolve_alert(alert_id: int, db: DB) -> dict:
    if not nightly.resolve_alert(db, alert_id):
        raise HTTPException(status_code=404, detail="no open alert with that id")
    return {"resolved": alert_id}


@router.post("/ingest", response_model=IngestStarted, status_code=202)
def start_ingest(runner: Runner) -> IngestStarted:
    return IngestStarted(started=runner.start())


@router.get("/ingest", response_model=services.IngestStatus)
def ingest_status(runner: Runner) -> services.IngestStatus:
    return runner.status
