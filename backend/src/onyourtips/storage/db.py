"""SQLite storage. Stdlib only, so it can be swapped for Postgres later behind the same methods.

Datetimes are stored as ISO-8601 UTC strings with second precision, so string comparison
matches chronological order.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from onyourtips.core.models import (
    CONTENT_TYPE_PRIORITY,
    Alert,
    Article,
    ArticleContent,
    ArticleState,
    ContentStatus,
    ContentType,
    Engagement,
    FeedItem,
    Interaction,
    InteractionType,
    Passage,
    PipelineRun,
    Sighting,
    SourceRunResult,
    UpsertOutcome,
)
from onyourtips.core.utils import parse_datetime, unique, utcnow
from onyourtips.ranking.scoring import compute_signal, rank_score
from onyourtips.storage.migrations import apply_migrations

if TYPE_CHECKING:  # runtime import would be circular (llm imports storage)
    from onyourtips.llm.models import LLMCallRecord

# Article ids whose most recent dismiss/undismiss event is a dismiss.
_DISMISSED_IDS = """
SELECT i.article_id FROM interactions i
WHERE i.action = 'dismiss' AND i.id = (
    SELECT MAX(j.id) FROM interactions j
    WHERE j.article_id = i.article_id AND j.action IN ('dismiss', 'undismiss'))
"""
# Article ids whose most recent save/unsave event is a save, with the save time.
_SAVED_IDS = """
SELECT i.article_id, i.created_at AS saved_at FROM interactions i
WHERE i.action = 'save' AND i.id = (
    SELECT MAX(j.id) FROM interactions j
    WHERE j.article_id = i.article_id AND j.action IN ('save', 'unsave'))
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the API opens one connection per request, and FastAPI may run the
        # dependency and endpoint on different threadpool threads. Never shared concurrently.
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        apply_migrations(self.conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ writes

    def upsert(
        self,
        article: Article,
        sighting: Sighting,
        weights: Mapping[str, float],
        title_window_days: int = 3,
    ) -> UpsertOutcome:
        with self.conn:
            existing = self._find_existing(article, title_window_days)
            if existing is None:
                self._insert_article(article)
                outcome = UpsertOutcome.NEW
                target_id = article.id
            else:
                target_id = existing.id
                had_source = self.conn.execute(
                    "SELECT 1 FROM sightings WHERE article_id=? AND source_id=?",
                    (target_id, sighting.source_id),
                ).fetchone()
                self._merge_into(existing, article)
                outcome = UpsertOutcome.UPDATED if had_source else UpsertOutcome.MERGED

            self._upsert_sighting(target_id, sighting)
            signal = compute_signal(self.get_sightings(target_id), weights)
            self.conn.execute("UPDATE articles SET signal=? WHERE id=?", (signal, target_id))
        return outcome

    def _find_existing(self, article: Article, window_days: int) -> Article | None:
        row = self.conn.execute("SELECT * FROM articles WHERE id=?", (article.id,)).fetchone()
        if row is None and article.title_fp:
            ref = article.published_at or article.first_seen_at
            row = self.conn.execute(
                """SELECT * FROM articles WHERE title_fp=?
                   AND COALESCE(published_at, first_seen_at) BETWEEN ? AND ?
                   ORDER BY first_seen_at LIMIT 1""",
                (
                    article.title_fp,
                    _iso(ref - timedelta(days=window_days)),
                    _iso(ref + timedelta(days=window_days)),
                ),
            ).fetchone()
        return self._row_to_article(row) if row else None

    def _insert_article(self, a: Article) -> None:
        self.conn.execute(
            """INSERT INTO articles (id, canonical_url, url, title, title_fp, summary, author,
               content_type, published_at, first_seen_at, last_seen_at, topics, source_tags,
               primary_source_id, signal, extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                a.id,
                a.canonical_url,
                a.url,
                a.title,
                a.title_fp,
                a.summary,
                a.author,
                a.content_type.value,
                _iso(a.published_at),
                _iso(a.first_seen_at),
                _iso(a.last_seen_at),
                json.dumps(a.topics),
                json.dumps(a.source_tags),
                a.primary_source_id,
                a.signal,
                json.dumps(a.extra, default=str),
            ),
        )

    def _merge_into(self, old: Article, new: Article) -> None:
        """Deterministic merge rules: keep earliest date, most specific type, union of tags."""
        dates = [d for d in (old.published_at, new.published_at) if d]
        content_type = max(
            (old.content_type, new.content_type), key=lambda c: CONTENT_TYPE_PRIORITY[c]
        )
        summary = (
            old.summary
            if old.summary and len(old.summary) >= len(new.summary or "")
            else new.summary
        )
        self.conn.execute(
            """UPDATE articles SET summary=?, author=?, content_type=?, published_at=?,
               last_seen_at=?, topics=?, source_tags=?, extra=? WHERE id=?""",
            (
                summary,
                old.author or new.author,
                content_type.value,
                _iso(min(dates)) if dates else None,
                _iso(max(old.last_seen_at, new.last_seen_at)),
                json.dumps(unique(old.topics + new.topics)),
                json.dumps(unique(old.source_tags + new.source_tags)),
                json.dumps({**new.extra, **old.extra}, default=str),
                old.id,
            ),
        )

    def _upsert_sighting(self, article_id: str, s: Sighting) -> None:
        e = s.engagement
        self.conn.execute(
            """INSERT INTO sightings (article_id, source_id, external_id, discussion_url,
                                      points, comments, stars, seen_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(article_id, source_id) DO UPDATE SET
                 external_id=COALESCE(excluded.external_id, external_id),
                 discussion_url=COALESCE(excluded.discussion_url, discussion_url),
                 points=COALESCE(excluded.points, points),
                 comments=COALESCE(excluded.comments, comments),
                 stars=COALESCE(excluded.stars, stars),
                 seen_at=excluded.seen_at""",
            (
                article_id,
                s.source_id,
                s.external_id,
                s.discussion_url,
                e.points,
                e.comments,
                e.stars,
                _iso(s.seen_at),
            ),
        )

    def record_run(
        self,
        run_id: str,
        started_at: datetime,
        finished_at: datetime,
        results: Iterable[SourceRunResult],
    ) -> None:
        with self.conn:
            self.conn.executemany(
                """INSERT OR REPLACE INTO ingestion_runs (run_id, source_id, started_at,
                   finished_at, ok, fetched, new, merged, updated, skipped, error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id,
                        r.source_id,
                        _iso(started_at),
                        _iso(finished_at),
                        int(r.ok),
                        r.fetched,
                        r.new,
                        r.merged,
                        r.updated,
                        r.skipped,
                        r.error,
                    )
                    for r in results
                ],
            )

    # ------------------------------------------------------------------ reads

    def get_article(self, article_id: str) -> Article | None:
        row = self.conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        return self._row_to_article(row) if row else None

    def get_sightings(self, article_id: str) -> list[Sighting]:
        rows = self.conn.execute(
            "SELECT * FROM sightings WHERE article_id=? ORDER BY seen_at", (article_id,)
        ).fetchall()
        return [self._row_to_sighting(r) for r in rows]

    def feed(
        self,
        *,
        since: datetime,
        topics: list[str] | None = None,
        content_types: list[ContentType] | None = None,
        source_ids: list[str] | None = None,
        query: str | None = None,
        limit: int = 50,
        half_life_hours: float | None = 24.0,
        min_signal: float | None = None,
        include_dismissed: bool = False,
        until: datetime | None = None,
        now: datetime | None = None,
    ) -> list[FeedItem]:
        """Ranked articles in a time window. ``half_life_hours=None`` disables recency decay
        (score == signal), which is what the weekly digest uses."""
        now = now or utcnow()
        where = ["COALESCE(a.published_at, a.first_seen_at) >= ?"]
        params: list[object] = [_iso(since)]
        if until:
            where.append("COALESCE(a.published_at, a.first_seen_at) <= ?")
            params.append(_iso(until))
        if min_signal is not None:
            where.append("a.signal >= ?")
            params.append(min_signal)
        if not include_dismissed:
            where.append(f"a.id NOT IN ({_DISMISSED_IDS})")
        if topics:
            marks = ",".join("?" * len(topics))
            where.append(f"EXISTS (SELECT 1 FROM json_each(a.topics) WHERE value IN ({marks}))")
            params += topics
        if content_types:
            where.append(f"a.content_type IN ({','.join('?' * len(content_types))})")
            params += [c.value for c in content_types]
        if source_ids:
            where.append(
                f"EXISTS (SELECT 1 FROM sightings s WHERE s.article_id=a.id "
                f"AND s.source_id IN ({','.join('?' * len(source_ids))}))"
            )
            params += source_ids
        if query:
            where.append("(a.title LIKE ? OR a.summary LIKE ?)")
            params += [f"%{query}%", f"%{query}%"]

        rows = self.conn.execute(
            f"SELECT a.* FROM articles a WHERE {' AND '.join(where)} "
            f"ORDER BY a.signal DESC LIMIT 5000",
            params,
        ).fetchall()

        items: list[FeedItem] = []
        for row in rows:
            art = self._row_to_article(row)
            ref = art.published_at or art.first_seen_at
            score = (
                art.signal
                if half_life_hours is None
                else rank_score(art.signal, ref, now, half_life_hours)
            )
            items.append(FeedItem(article=art, sightings=[], score=score))
        items.sort(key=lambda i: (i.score, i.article.signal), reverse=True)
        items = items[:limit]
        self._hydrate(items)
        return items

    def _hydrate(self, items: list[FeedItem]) -> None:
        states = self.get_states([i.article.id for i in items])
        for item in items:
            item.sightings = self.get_sightings(item.article.id)
            item.state = states.get(item.article.id, ArticleState())

    def resolve_id(self, prefix: str) -> str:
        """Accept a full id or a unique prefix (the CLI shows 8 chars)."""
        prefix = prefix.strip().lower()
        if len(prefix) < 4:
            raise ValueError("article id prefix must be at least 4 characters")
        rows = self.conn.execute(
            "SELECT id FROM articles WHERE id LIKE ? LIMIT 2", (prefix + "%",)
        ).fetchall()
        if not rows:
            raise KeyError(f"no article with id starting '{prefix}'")
        if len(rows) > 1:
            raise ValueError(f"id prefix '{prefix}' is ambiguous; use more characters")
        return rows[0]["id"]

    # ------------------------------------------------------------------ interactions

    def add_interaction(
        self,
        article_id: str,
        action: InteractionType,
        value: int | None = None,
        note: str | None = None,
        at: datetime | None = None,
    ) -> Interaction:
        if action is InteractionType.RATE and (value is None or not 1 <= value <= 5):
            raise ValueError("rating must be between 1 and 5")
        if self.get_article(article_id) is None:
            raise KeyError(f"unknown article {article_id}")
        it = Interaction(
            article_id=article_id, action=action, value=value, note=note, created_at=at or utcnow()
        )
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO interactions (article_id, action, value, note, created_at) "
                "VALUES (?,?,?,?,?)",
                (it.article_id, it.action.value, it.value, it.note, _iso(it.created_at)),
            )
        it.id = cur.lastrowid
        return it

    def get_interactions(
        self, article_id: str | None = None, since: datetime | None = None
    ) -> list[Interaction]:
        where, params = [], []
        if article_id:
            where.append("article_id = ?")
            params.append(article_id)
        if since:
            where.append("created_at >= ?")
            params.append(_iso(since))
        sql = "SELECT * FROM interactions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self.conn.execute(sql + " ORDER BY id", params).fetchall()
        return [
            Interaction(
                id=r["id"],
                article_id=r["article_id"],
                action=InteractionType(r["action"]),
                value=r["value"],
                note=r["note"],
                created_at=parse_datetime(r["created_at"]),
            )
            for r in rows
        ]

    def get_states(self, article_ids: list[str]) -> dict[str, ArticleState]:
        """Fold the event log into the current state per article."""
        states: dict[str, ArticleState] = {}
        for chunk_start in range(0, len(article_ids), 500):
            chunk = article_ids[chunk_start : chunk_start + 500]
            if not chunk:
                continue
            rows = self.conn.execute(
                f"SELECT * FROM interactions WHERE article_id IN ({','.join('?' * len(chunk))}) "
                f"ORDER BY id",
                chunk,
            ).fetchall()
            for r in rows:
                st = states.setdefault(r["article_id"], ArticleState())
                action, at = InteractionType(r["action"]), parse_datetime(r["created_at"])
                st.last_interaction_at = at
                if action is InteractionType.OPEN:
                    st.opened = True
                elif action is InteractionType.SAVE:
                    st.saved, st.saved_at = True, at
                elif action is InteractionType.UNSAVE:
                    st.saved, st.saved_at = False, None
                elif action is InteractionType.DISMISS:
                    st.dismissed = True
                elif action is InteractionType.UNDISMISS:
                    st.dismissed = False
                elif action is InteractionType.RATE:
                    st.rating = r["value"]
        return states

    def saved(self, since: datetime | None = None, limit: int = 200) -> list[FeedItem]:
        """Currently saved articles, most recently saved first. ``since`` filters on save time."""
        sql = f"SELECT a.*, s.saved_at FROM articles a JOIN ({_SAVED_IDS}) s ON s.article_id = a.id"
        params: list[object] = []
        if since:
            sql += " WHERE s.saved_at >= ?"
            params.append(_iso(since))
        rows = self.conn.execute(
            sql + " ORDER BY s.saved_at DESC LIMIT ?", [*params, limit]
        ).fetchall()
        items = [
            FeedItem(article=self._row_to_article(r), sightings=[], score=r["signal"]) for r in rows
        ]
        self._hydrate(items)
        return items

    def interest_profile(self, since: datetime) -> dict[str, float]:
        """Topic -> interest score from your interactions in the window (saves, opens,
        ratings count for; dismissals against). Seed for the future recommender."""
        ids = sorted({i.article_id for i in self.get_interactions(since=since)})
        states = self.get_states(ids)
        profile: dict[str, float] = {}
        for aid, st in states.items():
            art = self.get_article(aid)
            if art is None:
                continue
            for topic in art.topics or ["(untagged)"]:
                profile[topic] = profile.get(topic, 0.0) + st.interest_weight
        return dict(sorted(profile.items(), key=lambda kv: kv[1], reverse=True))

    # ------------------------------------------------------------------ LLM cost ledger

    def record_llm_call(self, r: LLMCallRecord) -> int:
        u = r.usage
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO llm_calls (created_at, purpose, article_id, provider, model,
                   prompt_version, attempt, status, error, input_tokens, output_tokens,
                   cache_read_tokens, cache_write_tokens, cost_usd, pricing_version, latency_ms,
                   tokens_per_sec, timings, request_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _iso(r.created_at),
                    r.purpose,
                    r.article_id,
                    r.provider,
                    r.model,
                    r.prompt_version,
                    r.attempt,
                    r.status,
                    r.error,
                    u.input_tokens,
                    u.output_tokens,
                    u.cache_read_tokens,
                    u.cache_write_tokens,
                    r.cost_usd,
                    r.pricing_version,
                    r.latency_ms,
                    r.tokens_per_sec,
                    json.dumps(r.timings),
                    r.request_id,
                ),
            )
        return int(cur.lastrowid)

    def llm_spend(self, since: datetime) -> dict[str, float]:
        """Calls (every attempt counts) and spend since a point in time. Used by budgets."""
        row = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE created_at >= ?",
            (_iso(since),),
        ).fetchone()
        return {"calls": int(row[0]), "cost_usd": float(row[1])}

    def llm_summary(self, since: datetime, by: str = "model") -> list[dict[str, object]]:
        """Aggregate the ledger by model, purpose or day."""
        key = {
            "model": "provider || '/' || model",
            "purpose": "purpose",
            "day": "substr(created_at, 1, 10)",
        }[by]
        rows = self.conn.execute(
            f"""SELECT {key} AS k,
                       COUNT(*) AS calls,
                       SUM(status = 'ok') AS ok,
                       SUM(status = 'invalid') AS invalid,
                       SUM(status = 'error') AS errors,
                       SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       ROUND(SUM(cost_usd), 6) AS cost_usd,
                       CAST(AVG(CASE WHEN status != 'error' THEN latency_ms END) AS INTEGER)
                           AS avg_latency_ms,
                       ROUND(AVG(tokens_per_sec), 1) AS avg_tokens_per_sec
                FROM llm_calls WHERE created_at >= ?
                GROUP BY k ORDER BY {"k DESC" if by == "day" else "calls DESC"}""",
            (_iso(since),),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ full text + passages

    def save_content(self, content: ArticleContent, passages: list[Passage]) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO article_content (article_id, content_hash, extractor,
                   extractor_version, source_url, text, word_count, status, error, fetched_at,
                   retryable, counts_as_attempt)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    content.article_id,
                    content.content_hash,
                    content.extractor,
                    content.extractor_version,
                    content.source_url,
                    content.text,
                    content.word_count,
                    content.status.value,
                    content.error,
                    _iso(content.fetched_at),
                    int(content.retryable),
                    int(content.counts_as_attempt),
                ),
            )
            if content.content_hash and passages:
                # Same text (same hash) always yields the same passages; keep the first copy.
                self.conn.executemany(
                    "INSERT OR IGNORE INTO passages VALUES (?,?,?,?,?)",
                    [
                        (content.content_hash, p.idx, p.text, p.char_start, p.char_end)
                        for p in passages
                    ],
                )
        return int(cur.lastrowid)

    def latest_content(self, article_id: str) -> ArticleContent | None:
        row = self.conn.execute(
            "SELECT * FROM article_content WHERE article_id=? ORDER BY id DESC LIMIT 1",
            (article_id,),
        ).fetchone()
        if row is None:
            return None
        return ArticleContent(
            id=row["id"],
            article_id=row["article_id"],
            content_hash=row["content_hash"],
            extractor=row["extractor"],
            extractor_version=row["extractor_version"],
            source_url=row["source_url"],
            text=row["text"],
            word_count=row["word_count"],
            status=ContentStatus(row["status"]),
            error=row["error"],
            retryable=bool(row["retryable"]),
            counts_as_attempt=bool(row["counts_as_attempt"]),
            fetched_at=parse_datetime(row["fetched_at"]),
        )

    def failed_attempts(self, article_id: str) -> int:
        """How many extraction attempts for this article failed transiently (not counting
        attempts made while the network was down)."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM article_content "
            "WHERE article_id=? AND retryable=1 AND counts_as_attempt=1",
            (article_id,),
        ).fetchone()[0]

    def extraction_candidates(self, since: datetime, max_attempts: int = 2) -> list[str]:
        """Articles first seen since `since` that need (another) extraction attempt: never
        attempted, or the last attempt failed transiently and fewer than `max_attempts`
        attempts have failed. Highest signal first."""
        rows = self.conn.execute(
            f"""SELECT a.id FROM articles a
                LEFT JOIN article_content c
                  ON c.id = (SELECT MAX(id) FROM article_content WHERE article_id = a.id)
                WHERE a.first_seen_at >= ?
                  AND a.id NOT IN ({_DISMISSED_IDS})
                  AND (c.id IS NULL
                       OR (c.retryable = 1
                           AND (SELECT COUNT(*) FROM article_content
                                WHERE article_id = a.id AND retryable = 1
                                  AND counts_as_attempt = 1) < ?))
                ORDER BY a.signal DESC""",
            (_iso(since), max_attempts),
        ).fetchall()
        return [r[0] for r in rows]

    def update_topics(self, article_id: str, topics: list[str]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE articles SET topics=? WHERE id=?", (json.dumps(topics), article_id)
            )

    # ------------------------------------------------------------------ pipeline runs + alerts

    def start_pipeline_run(self, run: PipelineRun) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO pipeline_runs (run_id, trigger, started_at, status, steps) "
                "VALUES (?,?,?,?,?)",
                (run.run_id, run.trigger, _iso(run.started_at), run.status, json.dumps(run.steps)),
            )

    def finish_pipeline_run(
        self,
        run_id: str,
        *,
        status: str,
        steps: dict,
        finished_at: datetime,
        error: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE pipeline_runs SET status=?, steps=?, finished_at=?, error=? WHERE run_id=?",
                (status, json.dumps(steps, default=str), _iso(finished_at), error, run_id),
            )

    def last_pipeline_run(self, trigger: str | None = None) -> PipelineRun | None:
        sql = "SELECT * FROM pipeline_runs"
        params: list[object] = []
        if trigger:
            sql += " WHERE trigger=?"
            params.append(trigger)
        row = self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
        if row is None:
            return None
        return PipelineRun(
            id=row["id"],
            run_id=row["run_id"],
            trigger=row["trigger"],
            started_at=parse_datetime(row["started_at"]),
            finished_at=parse_datetime(row["finished_at"]),
            status=row["status"],
            steps=json.loads(row["steps"]),
            error=row["error"],
        )

    def source_failure_streaks(self) -> dict[str, tuple[int, str | None]]:
        """Per source: how many of its most recent runs failed in a row, and the latest error."""
        rows = self.conn.execute(
            "SELECT source_id, ok, error FROM ingestion_runs WHERE counts = 1 "
            "ORDER BY started_at DESC, rowid DESC"
        ).fetchall()
        streaks: dict[str, tuple[int, str | None]] = {}
        settled: set[str] = set()
        for r in rows:
            sid = r["source_id"]
            if sid in settled:
                continue
            if r["ok"]:
                settled.add(sid)
                streaks.setdefault(sid, (0, None))
            else:
                count, err = streaks.get(sid, (0, None))
                streaks[sid] = (count + 1, err or r["error"])  # keep the most recent error
        return streaks

    def discount_outage_failures(self, run_id: str, article_ids: list[str]) -> None:
        """The network was down during this run: its source failures and these articles' latest
        failed attempts don't count toward streaks or the give-up limit."""
        with self.conn:
            self.conn.execute(
                "UPDATE ingestion_runs SET counts=0 WHERE run_id=? AND ok=0", (run_id,)
            )
            self.conn.executemany(
                """UPDATE article_content SET counts_as_attempt=0
                   WHERE id = (SELECT MAX(id) FROM article_content WHERE article_id=?)
                     AND retryable=1""",
                [(a,) for a in article_ids],
            )

    def add_alert(self, alert: Alert) -> tuple[int, bool]:
        """Open an alert, or refresh the open one with the same kind and subject.
        Returns (alert id, whether it is new)."""
        existing = self.conn.execute(
            "SELECT id FROM alerts WHERE kind=? AND subject=? AND resolved_at IS NULL",
            (alert.kind, alert.subject),
        ).fetchone()
        details = json.dumps(alert.details, default=str)
        with self.conn:
            if existing:
                self.conn.execute(
                    "UPDATE alerts SET message=?, details=? WHERE id=?",
                    (alert.message, details, existing["id"]),
                )
                return int(existing["id"]), False
            cur = self.conn.execute(
                "INSERT INTO alerts (created_at, kind, subject, message, details) "
                "VALUES (?,?,?,?,?)",
                (_iso(alert.created_at), alert.kind, alert.subject, alert.message, details),
            )
        return int(cur.lastrowid), True

    def list_alerts(self, include_resolved: bool = False, limit: int = 100) -> list[Alert]:
        sql = "SELECT * FROM alerts"
        if not include_resolved:
            sql += " WHERE resolved_at IS NULL"
        rows = self.conn.execute(sql + " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [
            Alert(
                id=r["id"],
                created_at=parse_datetime(r["created_at"]),
                kind=r["kind"],
                subject=r["subject"],
                message=r["message"],
                details=json.loads(r["details"]),
                resolved_at=parse_datetime(r["resolved_at"]),
            )
            for r in rows
        ]

    def resolve_alert(self, alert_id: int, at: datetime | None = None) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE alerts SET resolved_at=? WHERE id=? AND resolved_at IS NULL",
                (_iso(at or utcnow()), alert_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------ token counts

    def save_token_count(self, row: dict) -> None:
        cols = (
            "article_id content_hash prompt_version method model chars_per_token system_chars "
            "article_chars system_tokens article_tokens input_tokens output_budget llm_call_id "
            "created_at tokenizer"
        ).split()
        with self.conn:
            self.conn.execute(
                f"INSERT OR REPLACE INTO token_counts ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})",
                [row.get(c) for c in cols],
            )

    def usable_contents(self, since: datetime | None = None) -> list[tuple[str, str]]:
        """(article id, content hash) of each article's latest usable extraction
        (ok or excerpt, with passages), optionally only articles first seen since `since`."""
        rows = self.conn.execute(
            """SELECT a.id, c.content_hash FROM articles a
               JOIN article_content c
                 ON c.id = (SELECT MAX(id) FROM article_content WHERE article_id = a.id)
               WHERE c.status IN ('ok', 'excerpt') AND c.content_hash IS NOT NULL
                 AND a.first_seen_at >= ?""",
            (_iso(since) if since else "",),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def measured_ratio(self, model: str, prompt_version: str) -> tuple[float | None, int]:
        """Characters per token observed for this model (weighted by length), and sample size."""
        row = self.conn.execute(
            """SELECT SUM(article_chars), SUM(article_tokens), COUNT(*) FROM token_counts
               WHERE method='measured' AND model=? AND prompt_version=?""",
            (model, prompt_version),
        ).fetchone()
        chars, tokens, n = row[0] or 0, row[1] or 0, row[2] or 0
        return (chars / tokens if tokens else None), n

    def measured_system_tokens(self, model: str, prompt_version: str) -> int | None:
        row = self.conn.execute(
            """SELECT system_tokens FROM token_counts
               WHERE method='measured' AND model=? AND prompt_version=? AND system_tokens > 0
               ORDER BY id DESC LIMIT 1""",
            (model, prompt_version),
        ).fetchone()
        return row[0] if row else None

    def measured_article_ids(self, model: str, prompt_version: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT article_id FROM token_counts WHERE method='measured' AND model=? "
            "AND prompt_version=?",
            (model, prompt_version),
        ).fetchall()
        return {r[0] for r in rows}

    def token_counts(
        self, model: str, prompt_version: str, method: str, since: datetime | None = None
    ) -> list[dict]:
        """Counts for each article's current text, joined with its extraction status."""
        rows = self.conn.execute(
            """SELECT t.*, c.status, a.title, a.first_seen_at FROM token_counts t
               JOIN articles a ON a.id = t.article_id
               JOIN article_content c
                 ON c.id = (SELECT MAX(id) FROM article_content WHERE article_id = a.id)
                AND c.content_hash = t.content_hash
               WHERE t.model=? AND t.prompt_version=? AND t.method=? AND a.first_seen_at >= ?""",
            (model, prompt_version, method, _iso(since) if since else ""),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ summaries

    _SUMMARY_COLS = (
        "article_id content_hash prompt_version provider model tokenizer estimated_input_tokens "
        "estimated_output_tokens estimated_cost_usd real_input_tokens real_output_tokens "
        "real_cost_usd summarization_done description summary_json llm_call_id latency_ms "
        "created_at"
    ).split()

    def save_summary(self, row: dict) -> int:
        cols = self._SUMMARY_COLS
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO summaries ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [row.get(c) for c in cols],
            )
        return int(cur.lastrowid)

    def list_summaries(self, article_id: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM summaries"
        args: list = []
        if article_id:
            sql += " WHERE article_id = ?"
            args.append(article_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def get_passages(self, content_hash: str) -> list[Passage]:
        rows = self.conn.execute(
            "SELECT idx, text, char_start, char_end FROM passages "
            "WHERE content_hash=? ORDER BY idx",
            (content_hash,),
        ).fetchall()
        return [Passage(**dict(r)) for r in rows]

    def extracted_ids(self, article_ids: list[str]) -> set[str]:
        """Articles whose latest extraction is final: usable text (ok or excerpt) and not a
        transient failure waiting to be retried."""
        done: set[str] = set()
        for i in range(0, len(article_ids), 500):
            chunk = article_ids[i : i + 500]
            if not chunk:
                continue
            rows = self.conn.execute(
                f"""SELECT c.article_id FROM article_content c
                    WHERE c.article_id IN ({",".join("?" * len(chunk))})
                      AND c.id = (SELECT MAX(id) FROM article_content
                                  WHERE article_id = c.article_id)
                      AND c.status IN ('ok', 'excerpt') AND c.retryable = 0""",
                chunk,
            ).fetchall()
            done.update(r[0] for r in rows)
        return done

    def topic_counts(self, since: datetime) -> dict[str, int]:
        """Number of (non-dismissed) articles per topic published since ``since``."""
        rows = self.conn.execute(
            "SELECT j.value, COUNT(*) FROM articles a, json_each(a.topics) j "
            "WHERE COALESCE(a.published_at, a.first_seen_at) >= ? "
            f"AND a.id NOT IN ({_DISMISSED_IDS}) GROUP BY 1",
            (_iso(since),),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def stats(self) -> dict[str, object]:
        c = self.conn
        return {
            "articles": c.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
            "sightings": c.execute("SELECT COUNT(*) FROM sightings").fetchone()[0],
            "interactions": c.execute("SELECT COUNT(*) FROM interactions").fetchone()[0],
            "saved": c.execute(f"SELECT COUNT(*) FROM ({_SAVED_IDS})").fetchone()[0],
            "by_content_type": dict(
                c.execute(
                    "SELECT content_type, COUNT(*) FROM articles GROUP BY 1 ORDER BY 2 DESC"
                ).fetchall()
            ),
            "by_source": dict(
                c.execute(
                    "SELECT source_id, COUNT(*) FROM sightings GROUP BY 1 ORDER BY 2 DESC"
                ).fetchall()
            ),
            "by_topic": dict(
                c.execute(
                    "SELECT j.value, COUNT(*) FROM articles, json_each(articles.topics) j "
                    "GROUP BY 1 ORDER BY 2 DESC"
                ).fetchall()
            ),
            "last_run": [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM ingestion_runs WHERE run_id = "
                    "(SELECT run_id FROM ingestion_runs ORDER BY started_at DESC LIMIT 1) "
                    "ORDER BY source_id"
                ).fetchall()
            ],
        }

    # ------------------------------------------------------------------ mapping

    @staticmethod
    def _row_to_article(row: sqlite3.Row) -> Article:
        return Article(
            id=row["id"],
            canonical_url=row["canonical_url"],
            url=row["url"],
            title=row["title"],
            title_fp=row["title_fp"],
            summary=row["summary"],
            author=row["author"],
            content_type=ContentType(row["content_type"]),
            published_at=parse_datetime(row["published_at"]),
            first_seen_at=parse_datetime(row["first_seen_at"]),
            last_seen_at=parse_datetime(row["last_seen_at"]),
            topics=json.loads(row["topics"]),
            source_tags=json.loads(row["source_tags"]),
            primary_source_id=row["primary_source_id"],
            signal=row["signal"],
            extra=json.loads(row["extra"]),
        )

    @staticmethod
    def _row_to_sighting(row: sqlite3.Row) -> Sighting:
        return Sighting(
            source_id=row["source_id"],
            external_id=row["external_id"],
            discussion_url=row["discussion_url"],
            engagement=Engagement(
                points=row["points"], comments=row["comments"], stars=row["stars"]
            ),
            seen_at=parse_datetime(row["seen_at"]),
        )
