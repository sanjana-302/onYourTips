"""The daily refresh pipeline (ADR 0005). Runs at 1 AM via launchd, or from the Refresh button.

    1. ingest     best of the last 24 hours from every source
    2. extract    full text for every new article (no AI): numbered passages
                  + retry yesterday's transient failures; give up after 2 failed attempts
    3. retag      re-run the keyword tagger on title + summary + the start of the full text
       tokens     estimate how many tokens a summary of each article would use (no model)
    4b. summarize summaries with citations for every new story, best first, until the
                  morning deadline (summary_deadline, 08:30). Local model; see ADR 0006.
    4. alerts     source failing 2 runs in a row, pages given up on, pipeline errors
                  (stored in `alerts`, shown in the app, and a macOS notification)
    5. record     one `pipeline_runs` row with per-step counts (manual runs included, so extra
                  refreshes can become a paid feature later)

Network outages: before starting, the pipeline checks that it can resolve a couple of hostnames.
If it can't (Wi-Fi down, DNS failing), the run is recorded as `skipped` with one `network_down`
alert and nothing counts as a failure. If the network drops mid-run (most sources or page fetches
fail with DNS / no-route errors), those failures are discounted the same way and one
`network_down` alert replaces the per-source and per-website ones.

Everything here is deterministic: no LLM calls, so no AI cost.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import socket
import subprocess
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from onyourtips.core.http import HttpClient
from onyourtips.core.models import Alert, ContentStatus, PipelineRun
from onyourtips.core.settings import Settings
from onyourtips.core.utils import is_network_error, utcnow
from onyourtips.ingestion.pipeline import Pipeline
from onyourtips.ingestion.sources import load_source_configs
from onyourtips.ingestion.tagging import Tagger
from onyourtips.llm.models import BudgetExceeded, LLMError
from onyourtips.services.content import extract_articles
from onyourtips.services.tokens import estimate_articles
from onyourtips.storage import Database

log = logging.getLogger(__name__)


class NightlyReport(BaseModel):
    run_id: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"  # ok | partial (some step had failures) | failed | skipped (offline)
    network_outage: bool = False  # the network went down during the run; failures discounted
    ingest: dict = Field(default_factory=dict)
    extract: dict = Field(default_factory=dict)
    retag: dict = Field(default_factory=dict)
    tokens: dict = Field(default_factory=dict)
    summaries: dict = Field(default_factory=dict)
    new_alerts: list[Alert] = Field(default_factory=list)
    error: str | None = None

    def steps(self) -> dict:
        return {
            "ingest": self.ingest,
            "extract": self.extract,
            "retag": self.retag,
            "tokens": self.tokens,
            "summaries": self.summaries,
            "new_alerts": len(self.new_alerts),
            "network_outage": self.network_outage,
        }


def next_refresh_at(settings: Settings, now: datetime | None = None) -> datetime:
    """Next scheduled run, in the local time zone of the machine running the schedule."""
    local_now = now if now is not None else datetime.now().astimezone()
    target = local_now.replace(
        hour=settings.refresh_hour, minute=settings.refresh_minute, second=0, microsecond=0
    )
    return target if target > local_now else target + timedelta(days=1)


def run_nightly(
    settings: Settings,
    db: Database,
    *,
    trigger: str = "scheduled",
    ingest_http: HttpClient | None = None,
    extract_http: httpx.AsyncClient | None = None,
    notify=None,
    check_network=None,
    summarize: bool | None = None,
    llm_gateway=None,
    clock=None,
) -> NightlyReport:
    """Run the whole pipeline. HTTP clients, `notify`, `check_network`, the LLM gateway and the
    clock can be injected for tests. `summarize=None` follows settings.summary_nightly."""
    report = NightlyReport(run_id=uuid.uuid4().hex[:12], trigger=trigger, started_at=utcnow())
    db.start_pipeline_run(
        PipelineRun(
            run_id=report.run_id, trigger=trigger, started_at=report.started_at, status="running"
        )
    )
    tagger = Tagger.from_yaml(settings.taxonomy_file)
    problems = False
    try:
        if not (check_network or network_available)():
            # Offline: fetching anything would only produce hundreds of meaningless failures.
            report.status = "skipped"
            report.error = "No internet connection (DNS lookups failed); nothing was fetched."
            alert, new = _network_down_alert(
                db,
                f"The {trigger} refresh was skipped: this computer couldn't reach the internet. "
                "Nothing was counted as a failure; it will run again at the next refresh.",
                {"run_id": report.run_id, "when": "preflight"},
            )
            if new:
                report.new_alerts.append(alert)
        else:
            problems |= _ingest(settings, db, tagger, report, ingest_http)
            processed = _extract(settings, db, report, extract_http)
            problems |= bool(report.extract.get("gave_up"))
            _retag(settings, db, tagger, processed, report)
            since = utcnow() - timedelta(days=settings.extract_window_days)
            report.tokens = estimate_articles(settings, db, since=since)
            if report.network_outage:
                problems = True
                alert, new = _network_down_alert(
                    db,
                    "The network dropped during the refresh. Pages and sources that failed because "
                    "of it weren't counted as failures and will be tried again next time.",
                    {"run_id": report.run_id, "when": "mid-run"},
                )
                if new:
                    report.new_alerts.append(alert)
            else:
                _resolve_network_alert(db)
            report.new_alerts += _source_alerts(settings, db)
            if settings.summary_nightly if summarize is None else summarize:
                problems |= _summarize(settings, db, report, since, llm_gateway, clock)
            report.status = "partial" if problems else "ok"
    except Exception as exc:  # a broken step must still leave a record and an alert
        log.exception("refresh pipeline failed")
        report.status = "failed"
        report.error = f"{type(exc).__name__}: {exc}"
        alert, new = _alert(
            db,
            "pipeline_failed",
            "pipeline",
            f"The {trigger} refresh failed: {report.error}",
            {"run_id": report.run_id},
        )
        if new:
            report.new_alerts.append(alert)
    report.finished_at = utcnow()
    db.finish_pipeline_run(
        report.run_id,
        status=report.status,
        steps=report.steps(),
        finished_at=report.finished_at,
        error=report.error,
    )
    if report.new_alerts:
        (notify or _notify_desktop)(settings, report)
    return report


# ----------------------------------------------------------------------------- network

# Hosts the pipeline needs most; if none of them resolves, the machine is offline.
PREFLIGHT_HOSTS = ("hn.algolia.com", "api.github.com", "export.arxiv.org")
# Mid-run outage: at least this many network failures, making up at least this share of attempts.
OUTAGE_MIN_FAILURES = 5
OUTAGE_MIN_SHARE = 0.5


def network_available(hosts: tuple[str, ...] = PREFLIGHT_HOSTS, timeout: float = 5.0) -> bool:
    """True if at least one host name resolves (DNS works). No HTTP request is made."""

    async def resolve(host: str) -> bool:
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(loop.getaddrinfo(host, 443), timeout)
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def any_ok() -> bool:
        return any(await asyncio.gather(*(resolve(h) for h in hosts)))

    try:
        return asyncio.run(any_ok())
    except (OSError, socket.gaierror):
        return False


def _is_outage(network_failures: int, attempts: int) -> bool:
    return (
        network_failures >= OUTAGE_MIN_FAILURES
        and attempts > 0
        and network_failures / attempts >= OUTAGE_MIN_SHARE
    )


def _network_down_alert(db, message: str, details: dict) -> tuple[Alert, bool]:
    return _alert(db, "network_down", "network", message, details)


def _resolve_network_alert(db) -> None:
    for a in db.list_alerts():
        if a.kind == "network_down":
            db.resolve_alert(a.id)


# ----------------------------------------------------------------------------- steps


def _ingest(settings, db, tagger, report, http) -> bool:
    configs = load_source_configs(settings.sources_file)
    run = asyncio.run(Pipeline(settings, configs, tagger, db).run(http=http))
    failed = [r.source_id for r in run.results if not r.ok]
    offline = [r.source_id for r in run.results if not r.ok and is_network_error(r.error)]
    report.ingest = {**run.totals, "sources": len(run.results), "failed_sources": failed}
    if _is_outage(len(offline), len(run.results)):
        report.network_outage = True
        db.discount_outage_failures(run.run_id, [])
        report.ingest["network_failures"] = len(offline)
    return bool(failed)


def _extract(settings, db, report, http) -> list[str]:
    since = utcnow() - timedelta(days=settings.extract_window_days)
    candidates = db.extraction_candidates(since, max_attempts=settings.extract_max_attempts)
    retries = [a for a in candidates if db.failed_attempts(a) > 0]
    results = asyncio.run(extract_articles(db, settings, candidates, http=http))
    statuses = Counter(r.status for r in results)

    # Was the network down? Count fetches that failed on DNS / no route among all real fetches.
    latest_by_id = {r.article_id: db.latest_content(r.article_id) for r in results if not r.cached}
    fetched = [c for c in latest_by_id.values() if c and (c.retryable or c.extractor != "feed")]
    offline = [c.article_id for c in fetched if c.retryable and is_network_error(c.error)]
    # If ingest already saw the outage, every network failure in this run is part of it.
    outage = bool(offline) and (report.network_outage or _is_outage(len(offline), len(fetched)))
    if outage:
        report.network_outage = True
        db.discount_outage_failures("", offline)

    retry_scheduled, gave_up = [], []
    for r in results:
        latest = db.latest_content(r.article_id)
        if latest is None or not latest.retryable:
            continue
        if not latest.counts_as_attempt:
            retry_scheduled.append(r)  # failed during the outage: try again, attempt not used
        elif db.failed_attempts(r.article_id) >= settings.extract_max_attempts:
            gave_up.append(r)
        else:
            retry_scheduled.append(r)

    report.extract = {
        "candidates": len(candidates),
        "retries": len(retries),
        **{s.value: statuses.get(s.value, 0) for s in ContentStatus},
        "retry_scheduled": len(retry_scheduled),
        "gave_up": len(gave_up),
        "network_failures": len(offline) if outage else 0,
    }
    report.new_alerts += _gave_up_alerts(db, gave_up)
    return [r.article_id for r in results if r.status == ContentStatus.OK.value]


def _retag(settings, db, tagger, article_ids, report) -> None:
    """Topic tags from the full text's opening, not just the feed's one-line blurb.

    Only the first `retag_lede_chars` characters are used: the opening says what a story is
    about, while the whole text mentions everything in passing and would over-tag.
    """
    changed = 0
    for aid in article_ids:
        art = db.get_article(aid)
        content = db.latest_content(aid)
        if art is None or content is None or not content.text:
            continue
        lede = content.text[: settings.retag_lede_chars]
        topics = tagger.tag(art.title, f"{art.summary or ''}\n{lede}", art.source_tags)
        if topics != art.topics:
            db.update_topics(aid, topics)
            changed += 1
    report.retag = {"checked": len(article_ids), "changed": changed}


# ----------------------------------------------------------------------------- alerts


def _alert(db, kind, subject, message, details) -> tuple[Alert, bool]:
    alert = Alert(created_at=utcnow(), kind=kind, subject=subject, message=message, details=details)
    alert.id, new = db.add_alert(alert)
    return alert, new


def _gave_up_alerts(db, gave_up) -> list[Alert]:
    """One alert per website, since many failures on one site usually share one cause."""
    by_domain: dict[str, list] = defaultdict(list)
    for r in gave_up:
        art = db.get_article(r.article_id)
        domain = (
            (urlsplit(art.url).hostname or "unknown").removeprefix("www.") if art else "unknown"
        )
        by_domain[domain].append({"id": r.article_id, "title": r.title, "error": r.note})
    new_alerts = []
    for domain, pages in by_domain.items():
        n = len(pages)
        alert, new = _alert(
            db,
            "extraction_gave_up",
            domain,
            f"{n} {'page' if n == 1 else 'pages'} from {domain} failed twice and won't be retried. "
            f"Last error: {pages[-1]['error']}",
            {"pages": pages},
        )
        if new:
            new_alerts.append(alert)
    return new_alerts


def _source_alerts(settings, db) -> list[Alert]:
    """Alert when a source fails N runs in a row; resolve the alert once it works again."""
    names = {c.id: c.name for c in load_source_configs(settings.sources_file)}
    open_alerts = {(a.kind, a.subject): a for a in db.list_alerts()}
    new_alerts = []
    for source_id, (streak, error) in db.source_failure_streaks().items():
        key = ("source_failing", source_id)
        if streak >= settings.alert_source_failing_runs:
            alert, new = _alert(
                db,
                "source_failing",
                source_id,
                f"{names.get(source_id, source_id)} failed {streak} refreshes in a row: {error}",
                {"streak": streak, "error": error},
            )
            if new:
                new_alerts.append(alert)
        elif streak == 0 and key in open_alerts:
            db.resolve_alert(open_alerts[key].id)  # it works again
    return new_alerts


# ----------------------------------------------------------------------------- summarize


def summary_deadline_at(settings: Settings, start: datetime) -> datetime:
    """The next `summary_deadline` (local time) after `start`."""
    local = start.astimezone()
    hh, mm = (int(x) for x in settings.summary_deadline.split(":"))
    target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return target if target > local else target + timedelta(days=1)


def _summarize(settings, db, report: NightlyReport, since, gateway, clock) -> bool:
    """Summarize new stories until done or the deadline. Returns True if there were problems."""
    from onyourtips.enrichment.summary_prompt import SUMMARY_PROMPT_VERSION
    from onyourtips.llm import LLMGateway
    from onyourtips.services.summaries import summarize_article

    now = clock or (lambda: datetime.now().astimezone())
    started = now()
    deadline = summary_deadline_at(settings, started)
    gw = gateway or LLMGateway(settings, db)
    model = settings.llm_model
    stats = {
        "candidates": 0,
        "done": 0,
        "failed": 0,
        "map_reduce": 0,
        "not_reached": 0,
        "deadline": deadline.strftime("%H:%M"),
        "model": model,
    }

    # Is the model there at all? One clear alert beats 500 identical failures.
    try:
        names = {m.name for m in gw.provider.list_models()}
    except LLMError as exc:
        names, problem = None, f"the model server isn't reachable ({exc})"
    else:
        problem = (
            None
            if model in names or f"{model}:latest" in names
            else (f"model {model} isn't downloaded (ollama pull {model})")
        )
    if problem:
        stats["skipped"] = problem
        report.summaries = stats
        alert, new = _alert(
            db,
            "llm_unavailable",
            "llm",
            f"Summaries were skipped: {problem}. Stories will be summarized at "
            "the next refresh, or when you open them.",
            {"run_id": report.run_id},
        )
        if new:
            report.new_alerts.append(alert)
        return True
    for a in db.list_alerts():
        if a.kind == "llm_unavailable":
            db.resolve_alert(a.id)

    todo = db.summary_candidates(
        since, model, SUMMARY_PROMPT_VERSION, settings.summary_max_attempts
    )
    stats["candidates"] = len(todo)
    for i, article_id in enumerate(todo):
        if now() >= deadline:
            stats["not_reached"] = len(todo) - i
            break
        try:
            out = summarize_article(settings, db, article_id, gateway=gw, trigger="nightly")
        except BudgetExceeded as exc:
            stats["stopped"] = str(exc)
            stats["not_reached"] = len(todo) - i
            break
        if out.done:
            stats["done"] += 1
            stats["map_reduce"] += out.mode == "map_reduce"
        else:
            stats["failed"] += 1
    stats["minutes"] = round((now() - started).total_seconds() / 60, 1)
    report.summaries = stats
    if stats["not_reached"]:
        alert, new = _alert(
            db,
            "summaries_unfinished",
            "summaries",
            f"{stats['not_reached']} stories weren't summarized before {stats['deadline']}. "
            "They'll be done at the next refresh or when you open them.",
            {"run_id": report.run_id, **stats},
        )
        if new:
            report.new_alerts.append(alert)
    return bool(stats["failed"] or stats["not_reached"] or stats.get("stopped"))


def _notify_desktop(settings: Settings, report: NightlyReport) -> None:
    """Best-effort macOS notification. Never fails the pipeline."""
    if not settings.notify_desktop or platform.system() != "Darwin":
        return
    n = len(report.new_alerts)
    text = f"{n} new {'alert' if n == 1 else 'alerts'}: {report.new_alerts[0].message[:120]}"
    script = f'display notification "{_escape(text)}" with title "OnYourTips refresh"'
    try:
        subprocess.run(["osascript", "-e", script], timeout=10, check=False, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        log.info("desktop notification unavailable")


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ----------------------------------------------------------------------------- alert use cases


def list_alerts(db: Database, include_resolved: bool = False) -> list[Alert]:
    return db.list_alerts(include_resolved=include_resolved)


def resolve_alert(db: Database, alert_id: int) -> bool:
    return db.resolve_alert(alert_id)
