"""The daily refresh pipeline over three simulated nights (mocked web, no network)."""

import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from onyourtips.core.http import HttpClient
from onyourtips.enrichment import extraction
from onyourtips.services import app_status
from onyourtips.services.nightly import next_refresh_at, run_nightly

LEDE = " ".join(
    "The lab released an open-weight large language model built for agentic tool use by AI agents."
    for _ in range(30)
)
PAGE = f"<html><body><article><h1>Release notes</h1><p>{LEDE}</p></article></body></html>"
NOW_TS = int(time.time())
DNS_ERROR = "[Errno 8] nodename nor servname provided, or not known"

SOURCES_YAML = """
sources:
  - id: hn-good
    name: Hacker News · Top of the day
    kind: hackernews
    content_type: news
    params: { tags: story, window_hours: 24, min_points: 50 }
  - id: hn-bad
    name: Hacker News · Broken mirror
    kind: hackernews
    content_type: news
    params: { tags: show_hn }
"""

HITS = [
    {
        "objectID": "1",
        "title": "A quiet release from a research lab",
        "url": "https://ok.test/post",
        "points": 300,
        "num_comments": 40,
        "created_at_i": NOW_TS - 3600,
    },
    {
        "objectID": "2",
        "title": "Server is having a bad week today",
        "url": "https://flaky.test/a",
        "points": 200,
        "num_comments": 10,
        "created_at_i": NOW_TS - 3600,
    },
    {
        "objectID": "3",
        "title": "This page has gone missing forever",
        "url": "https://gone.test/b",
        "points": 100,
        "num_comments": 5,
        "created_at_i": NOW_TS - 3600,
    },
]


class World:
    """Mocked web for one night. `hn_bad_ok` flips the broken source back to healthy."""

    def __init__(self, hn_bad_ok=False, dns_down=False):
        self.hn_bad_ok = hn_bad_ok
        self.dns_down = dns_down  # the Mac lost DNS after the preflight check passed
        self.hn_params = []
        self.page_hits = []

    def ingest_client(self):
        def handler(req: httpx.Request):
            tags = req.url.params.get("tags")
            self.hn_params.append(dict(req.url.params))
            if self.dns_down:
                raise httpx.ConnectError(DNS_ERROR, request=req)
            if tags == "show_hn" and not self.hn_bad_ok:
                return httpx.Response(500, json={"error": "down"})
            return httpx.Response(200, json={"hits": HITS if tags == "story" else []})

        return HttpClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)), retries=0, backoff=0
        )

    def extract_client(self):
        def handler(req: httpx.Request):
            if req.url.path == "/robots.txt":
                return httpx.Response(404)
            self.page_hits.append(req.url.host)
            if self.dns_down:
                raise httpx.ConnectError(DNS_ERROR, request=req)
            if req.url.host == "ok.test":
                return httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE)
            if req.url.host == "flaky.test":
                return httpx.Response(503)
            return httpx.Response(404)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.fixture
def env(settings, tmp_path, db, monkeypatch):
    monkeypatch.setattr(extraction, "trafilatura", None)
    src = tmp_path / "sources.yaml"
    src.write_text(SOURCES_YAML)
    settings.sources_file = src
    settings.notify_desktop = False
    notes = []

    def night(world=None, trigger="scheduled", online=True):
        w = world or World()
        report = run_nightly(
            settings,
            db,
            trigger=trigger,
            ingest_http=w.ingest_client(),
            extract_http=w.extract_client(),
            notify=lambda s, r: notes.append([a.kind for a in r.new_alerts]),
            check_network=lambda: online,
        )
        return report, w

    return settings, db, night, notes


def test_three_nights(env):
    settings, db, night, notes = env

    # ---- night 1: one source fails; one page times out (retry tomorrow), one is gone (no retry)
    r1, w1 = night()
    assert r1.status == "partial" and r1.ingest["failed_sources"] == ["hn-bad"]
    assert r1.ingest["new"] == 3
    e = r1.extract
    assert e["candidates"] == 3 and e["ok"] == 1 and e["failed"] == 2
    assert e["retry_scheduled"] == 1 and e["gave_up"] == 0 and e["retries"] == 0
    assert r1.new_alerts == [] and notes == []  # one failure isn't an alert yet
    # retag read the full text: the neutral title alone wouldn't produce these topics
    ok_article = next(
        i.article
        for i in db.feed(since=datetime(2000, 1, 1, tzinfo=timezone.utc))
        if i.article.url == "https://ok.test/post"
    )
    assert {"llms", "agents"} <= set(ok_article.topics) and r1.retag["changed"] == 1
    # the HN query asked for the last 24 hours
    good_q = next(p for p in w1.hn_params if p["tags"] == "story")
    assert "created_at_i>" in good_q["numericFilters"] and "points>=50" in good_q["numericFilters"]

    # ---- night 2: source fails again (alert); flaky page fails its 2nd attempt (give up + alert)
    r2, w2 = night()
    assert r2.extract["candidates"] == 1 and r2.extract["retries"] == 1
    assert r2.extract["gave_up"] == 1 and r2.extract["retry_scheduled"] == 0
    assert w2.page_hits == ["flaky.test"]  # nothing else re-fetched
    kinds = sorted(a.kind for a in r2.new_alerts)
    assert kinds == ["extraction_gave_up", "source_failing"]
    assert notes == [kinds] or sorted(notes[0]) == kinds
    gave_up = next(a for a in r2.new_alerts if a.kind == "extraction_gave_up")
    assert gave_up.subject == "flaky.test" and "won't be retried" in gave_up.message
    source = next(a for a in r2.new_alerts if a.kind == "source_failing")
    assert source.subject == "hn-bad" and "Broken mirror failed 2" in source.message

    # ---- night 3: source recovers (alert resolves itself); the given-up page is left alone
    r3, w3 = night(World(hn_bad_ok=True))
    assert r3.status == "ok" and r3.extract["candidates"] == 0 and w3.page_hits == []
    open_alerts = db.list_alerts()
    assert [a.kind for a in open_alerts] == ["extraction_gave_up"]
    assert len(db.list_alerts(include_resolved=True)) == 2

    # every night is recorded
    runs = db.conn.execute(
        "SELECT trigger, status, steps FROM pipeline_runs ORDER BY id"
    ).fetchall()
    assert [(r["trigger"], r["status"]) for r in runs] == [
        ("scheduled", "partial"),
        ("scheduled", "partial"),
        ("scheduled", "ok"),
    ]
    assert json.loads(runs[0]["steps"])["extract"]["ok"] == 1


def test_manual_refresh_is_recorded_and_alerts_not_duplicated(env):
    settings, db, night, _ = env
    night()
    night()  # hn-bad alert opens
    r, _ = night(trigger="manual")  # still failing: alert refreshed, not duplicated
    assert r.trigger == "manual" and r.new_alerts == []
    assert [a.subject for a in db.list_alerts() if a.kind == "source_failing"] == ["hn-bad"]
    last = db.last_pipeline_run()
    assert last.trigger == "manual" and db.last_pipeline_run("scheduled").trigger == "scheduled"
    st = app_status(db, settings)
    assert st.last_refresh_trigger == "manual" and st.open_alerts == 2 and st.next_refresh_at


def test_pipeline_failure_records_run_and_alert(env, monkeypatch):
    settings, db, night, notes = env
    from onyourtips.services import nightly

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(nightly, "_extract", boom)
    r, _ = night()
    assert r.status == "failed" and "disk full" in r.error
    assert [a.kind for a in r.new_alerts] == ["pipeline_failed"] and notes
    assert db.last_pipeline_run().status == "failed"


def test_next_refresh_time(settings):
    ist = timezone(timedelta(hours=5, minutes=30))
    before = datetime(2026, 11, 6, 0, 30, tzinfo=ist)
    after = datetime(2026, 11, 6, 9, 0, tzinfo=ist)
    assert next_refresh_at(settings, before) == datetime(2026, 11, 6, 1, 0, tzinfo=ist)
    assert next_refresh_at(settings, after) == datetime(2026, 11, 7, 1, 0, tzinfo=ist)


# ------------------------------------------------------------------------- network outages


def test_offline_run_is_skipped_and_counts_nothing(env):
    settings, db, night, notes = env
    r, w = night(online=False)
    assert r.status == "skipped" and r.ingest == {} and r.extract == {}
    assert w.hn_params == [] and w.page_hits == []  # nothing was fetched
    assert [a.kind for a in r.new_alerts] == ["network_down"] and notes == [["network_down"]]
    assert db.source_failure_streaks() == {}
    assert db.last_pipeline_run().status == "skipped"
    # a second offline night refreshes the same alert instead of stacking a new one
    r2, _ = night(online=False)
    assert r2.new_alerts == [] and len(db.list_alerts()) == 1
    # back online: the run works and the network alert resolves itself
    r3, _ = night()
    assert r3.status == "partial" and not any(a.kind == "network_down" for a in db.list_alerts())


def test_network_drop_mid_run_does_not_use_up_attempts(env, monkeypatch):
    from onyourtips.services import nightly

    monkeypatch.setattr(nightly, "OUTAGE_MIN_FAILURES", 2)  # the mock world is tiny
    settings, db, night, notes = env

    r1, _ = night()  # hn-bad fails (1 in a row), flaky.test 503 (attempt 1 of 2)
    flaky = next(a for a in db.extraction_candidates(datetime(2000, 1, 1, tzinfo=timezone.utc)))
    assert db.failed_attempts(flaky) == 1

    r2, _ = night(World(dns_down=True))  # preflight passed, then DNS died
    assert r2.network_outage and r2.status == "partial"
    assert r2.ingest["network_failures"] == 2 and r2.extract["network_failures"] >= 1
    assert r2.extract["gave_up"] == 0  # flaky.test's outage failure didn't count
    assert db.failed_attempts(flaky) == 1
    assert db.source_failure_streaks()["hn-bad"][0] == 1  # outage run ignored
    kinds = [a.kind for a in r2.new_alerts]
    assert kinds == ["network_down"]  # one alert, not one per source/site

    r3, _ = night()  # healthy network: flaky's real 2nd failure counts now
    assert not r3.network_outage
    assert r3.extract["gave_up"] == 1
    assert db.source_failure_streaks()["hn-bad"][0] == 2
    open_kinds = {a.kind for a in db.list_alerts()}
    assert "network_down" not in open_kinds
    assert {"source_failing", "extraction_gave_up"} <= open_kinds


def test_migration_0006_repairs_dns_failures(db):
    from pathlib import Path

    from onyourtips.storage import db as dbmod

    conn = db.conn
    conn.execute("PRAGMA foreign_keys = OFF")  # bare rows, no parent articles needed
    rows = [  # run r1: every source failed on DNS (outage); run r2: one genuine failure
        ("r1", "a", 0, f"ConnectError: {DNS_ERROR}"),
        ("r1", "b", 0, f"ConnectError: {DNS_ERROR}"),
        ("r2", "a", 1, None),
        ("r2", "b", 0, "HTTP 500"),
    ]
    with conn:
        for run, src, ok, err in rows:
            conn.execute(
                "INSERT INTO ingestion_runs (run_id, source_id, started_at, finished_at, ok,"
                " fetched, new, merged, updated, skipped, error)"
                " VALUES (?,?,'2026-11-06T12:43:00Z','2026-11-06T12:43:15Z',?,0,0,0,0,0,?)",
                (run, src, ok, err),
            )
        for i, (err, retry) in enumerate(
            [(f"fetch failed: ConnectError: {DNS_ERROR}", 1), ("HTTP 503", 1)]
        ):
            conn.execute(
                "INSERT INTO article_content (article_id, extractor, extractor_version,"
                " source_url, word_count, status, error, fetched_at, retryable)"
                " VALUES (?, 'feed', '1', 'https://x.test', 0, 'failed', ?, '2026-11-06', ?)",
                (f"art{i}", err, retry),
            )
    sql = (Path(dbmod.__file__).parent / "migrations" / "0006_network_outages.sql").read_text()
    repair = sql[sql.index("-- One-time repair") :]
    conn.executescript(repair)

    assert db.failed_attempts("art0") == 0 and db.failed_attempts("art1") == 1
    counted = dict(
        ((r[0], r[1]), r[2])
        for r in conn.execute("SELECT run_id, source_id, counts FROM ingestion_runs")
    )
    assert counted == {("r1", "a"): 0, ("r1", "b"): 0, ("r2", "a"): 1, ("r2", "b"): 1}
