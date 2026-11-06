"""Full-text extraction against a mocked web (no network)."""

import asyncio

import httpx
import pytest

from onyourtips import services
from onyourtips.core.models import ContentStatus
from onyourtips.enrichment import extraction
from onyourtips.enrichment.extraction import Extractor, fallback_extract, markdown_to_text
from onyourtips.enrichment.passages import content_hash, normalize_text, split_passages
from tests.helpers import GH, HN, NOW, W
from tests.helpers import normalize_raw as _norm

LONG_P = " ".join(
    f"Sentence {i} explains the new model and its benchmark results." for i in range(40)
)
ARTICLE_HTML = f"""<html><head><title>T</title><script>var x=1;</script></head><body>
<nav><a href="/">Home</a> <a href="/about">About</a></nav>
<article><h1>Big launch</h1><p>{LONG_P}</p><p>Second paragraph with more detail about licensing.</p>
<ul><li>First bullet point about speed and cost.</li></ul></article>
<footer>Copyright 2026. Subscribe to our newsletter.</footer></body></html>"""


def web(routes):
    """MockTransport: routes maps (host, path) -> response kwargs or a callable."""
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        key = (req.url.host, req.url.path)
        if key not in routes:
            return httpx.Response(404)
        r = routes[key]
        return r(req) if callable(r) else httpx.Response(**r)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True), seen


def article(tagger, cfg, url, title="A sufficiently long article title here", summary=None):
    a, _ = _norm(tagger, cfg, url=url, title=title, summary=summary, published_at=NOW)
    return a


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_trafilatura(monkeypatch):
    # Tests exercise the built-in fallback so they behave the same everywhere.
    monkeypatch.setattr(extraction, "trafilatura", None)


# ----------------------------------------------------------------------------- pure helpers


def test_fallback_extract_keeps_article_drops_chrome():
    text = fallback_extract(ARTICLE_HTML)
    assert text.startswith("Big launch") and "Sentence 39" in text
    assert "Home" not in text and "Copyright" not in text and "var x" not in text
    assert "First bullet point" in text


def test_markdown_to_text():
    md = (
        "# AgentKit\n\n![build](https://img.shields.io/x.svg) [![ci](a)](b)\n\n"
        "Build **reliable** agents\nwith [MCP](https://modelcontextprotocol.io) tools.\n\n"
        "```python\npip install agentkit\n```\n\n- Fast\n- `Typed`\n"
    )
    t = normalize_text(markdown_to_text(md))
    assert "AgentKit" in t and "Build reliable agents with MCP tools." in t
    assert "shields" not in t and "pip install" not in t and "Typed" in t


def test_passage_offsets_and_limits():
    text = normalize_text("Intro\n\n" + LONG_P + "\n\nShort tail.")
    ps = split_passages(text, max_chars=400)
    assert [p.idx for p in ps] == list(range(1, len(ps) + 1))
    for p in ps:
        assert text[p.char_start : p.char_end] == p.text and len(p.text) <= 400
    assert ps[0].text.startswith("Intro\n\nSentence 0")  # heading merged forward
    assert ps[-1].text.endswith("Short tail.")  # short tail merged back
    assert content_hash(text) == content_hash(
        normalize_text("Intro\n\n" + LONG_P + "\n\nShort tail.")
    )


# ----------------------------------------------------------------------------- strategies


def test_web_page_extracted(settings, tagger):
    client, seen = web(
        {
            ("news.test", "/robots.txt"): {"status_code": 200, "text": "User-agent: *\nAllow: /"},
            ("news.test", "/launch"): {
                "status_code": 200,
                "headers": {"content-type": "text/html"},
                "text": ARTICLE_HTML,
            },
        }
    )
    a = article(tagger, HN, "https://news.test/launch")
    ex = run(Extractor(settings, client).extract(a))
    assert ex.content.status == ContentStatus.OK and ex.content.extractor == "html-fallback"
    assert ex.content.word_count > 300 and len(ex.passages) >= 2
    assert ex.content.content_hash == content_hash(ex.content.text)


def test_robots_disallow_falls_back_to_feed(settings, tagger):
    client, seen = web(
        {
            ("news.test", "/robots.txt"): {
                "status_code": 200,
                "text": "User-agent: *\nDisallow: /",
            },
        }
    )
    a = article(tagger, HN, "https://news.test/launch", summary="A short feed description.")
    ex = run(Extractor(settings, client).extract(a))
    assert ex.content.status == ContentStatus.EXCERPT and "robots.txt" in ex.content.error
    assert not any(u.endswith("/launch") for u in seen)  # page never requested


def test_robots_disallow_without_summary_is_skipped(settings, tagger):
    client, _ = web(
        {("news.test", "/robots.txt"): {"status_code": 200, "text": "User-agent: *\nDisallow: /"}}
    )
    ex = run(Extractor(settings, client).extract(article(tagger, HN, "https://news.test/x")))
    assert ex.content.status == ContentStatus.SKIPPED and ex.passages == []


def test_short_page_uses_feed_excerpt(settings, tagger):
    client, _ = web(
        {
            ("news.test", "/tiny"): {
                "status_code": 200,
                "headers": {"content-type": "text/html"},
                "text": "<html><body><p>Too short.</p></body></html>",
            },
        }
    )
    a = article(tagger, HN, "https://news.test/tiny", summary="Feed blurb about the story.")
    ex = run(Extractor(settings, client).extract(a))
    assert (
        ex.content.status == ContentStatus.EXCERPT
        and ex.content.text == "Feed blurb about the story."
    )
    assert "too short" in ex.content.error


def test_http_error_and_non_html(settings, tagger):
    client, _ = web(
        {
            ("news.test", "/gone"): {"status_code": 410},
            ("news.test", "/data"): {
                "status_code": 200,
                "headers": {"content-type": "application/json"},
                "text": "{}",
            },
        }
    )
    ext = Extractor(settings, client)
    gone = run(ext.extract(article(tagger, HN, "https://news.test/gone", summary="Blurb.")))
    assert gone.content.status == ContentStatus.EXCERPT and "HTTP 410" in gone.content.error
    data = run(ext.extract(article(tagger, HN, "https://news.test/data")))
    assert data.content.status == ContentStatus.SKIPPED


def test_network_failure_is_contained(settings, tagger):
    def boom(req):
        raise httpx.ConnectTimeout("timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    ex = run(
        Extractor(settings, client).extract(
            article(tagger, HN, "https://slow.test/a", summary="Blurb.")
        )
    )
    assert ex.content.status == ContentStatus.EXCERPT and "ConnectTimeout" in ex.content.error


def test_github_repo_uses_readme(settings, tagger):
    readme = "# AgentKit\n\n" + LONG_P
    seen_headers = {}

    def readme_route(req):
        seen_headers.update(req.headers)
        return httpx.Response(200, text=readme)

    client, seen = web({("api.github.com", "/repos/acme/agentkit/readme"): readme_route})
    a = article(
        tagger,
        GH,
        "https://github.com/acme/agentkit",
        title="acme/agentkit",
        summary="Build reliable AI agents.",
    )
    ex = run(Extractor(settings, client).extract(a))
    assert ex.content.extractor == "github-readme" and ex.content.status == ContentStatus.OK
    assert ex.content.text.startswith("Build reliable AI agents.")  # repo description first
    assert seen_headers["accept"] == "application/vnd.github.raw+json"
    assert seen == ["https://api.github.com/repos/acme/agentkit/readme"]


def test_arxiv_uses_feed_abstract_without_fetching(settings, tagger):
    client, seen = web({})
    a = article(
        tagger,
        HN,
        "https://arxiv.org/abs/2410.11111v2",
        summary="We study reasoning in small models.",
    )
    ex = run(Extractor(settings, client).extract(a))
    assert ex.content.status == ContentStatus.OK and ex.content.extractor == "feed"
    assert seen == []


def test_pdf_and_video_skipped_to_excerpt(settings, tagger):
    client, seen = web({})
    ext = Extractor(settings, client)
    pdf = run(ext.extract(article(tagger, HN, "https://x.test/paper.pdf", summary="Blurb.")))
    vid = run(ext.extract(article(tagger, HN, "https://www.youtube.com/watch?v=1")))
    assert (
        pdf.content.status == ContentStatus.EXCERPT and vid.content.status == ContentStatus.SKIPPED
    )
    assert seen == []


# ----------------------------------------------------------------------------- service + storage


def test_extract_service_saves_and_caches(settings, tagger, db):
    calls = {"n": 0}

    def page(req):
        calls["n"] += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, text=ARTICLE_HTML)

    client, _ = web({("news.test", "/launch"): page})
    a = article(tagger, HN, "https://news.test/launch")
    db.upsert(a, _norm(tagger, HN, url=a.url, title=a.title, published_at=NOW)[1], W)

    [r] = run(services.extract_articles(db, settings, [a.id], http=client))
    assert r.status == "ok" and r.passages >= 2 and not r.cached
    view = services.get_content(db, a.id[:8])
    assert view.content.word_count == r.words and len(view.passages) == r.passages
    assert view.passages[0].idx == 1

    [again] = run(services.extract_articles(db, settings, [a.id], http=client))
    assert again.cached and calls["n"] == 1  # not fetched twice
    run(services.extract_articles(db, settings, [a.id], force=True, http=client))
    assert calls["n"] == 2
    assert db.extracted_ids([a.id, "nope"]) == {a.id}


def test_select_for_extraction(settings, tagger, db):
    from onyourtips.core.models import InteractionType

    a = article(tagger, HN, "https://news.test/one")
    db.upsert(a, _norm(tagger, HN, url=a.url, title=a.title, published_at=NOW)[1], W)
    db.add_interaction(a.id, InteractionType.SAVE)
    assert services.select_for_extraction(db, settings, saved=True) == [a.id]
    assert services.select_for_extraction(db, settings, ids=[a.id[:8]], saved=True) == [a.id]
