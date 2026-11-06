"""Connector tests against mocked HTTP responses (no network)."""

from pathlib import Path

import httpx
import pytest
import respx

from onyourtips.core.models import SourceConfig
from onyourtips.ingestion.sources import build_source, load_source_configs
from tests.helpers import CONFIG, FIXTURES

FEED = (FIXTURES / "sample_feed.xml").read_bytes()


def cfg(kind, **kw):
    return SourceConfig(id=f"t-{kind}", name=kind, kind=kind, content_type="news", **kw)


def test_shipped_sources_yaml_is_valid():
    configs = load_source_configs(CONFIG / "sources.yaml")
    assert len(configs) >= 10
    assert len({c.id for c in configs}) == len(configs)


def test_duplicate_ids_rejected(tmp_path: Path):
    p = tmp_path / "s.yaml"
    p.write_text("sources:\n" + "  - {id: a, name: a, kind: rss, content_type: news}\n" * 2)
    with pytest.raises(ValueError, match="duplicate"):
        load_source_configs(p)


@respx.mock
async def test_rss(http, settings):
    respx.get("https://example.com/feed").respond(content=FEED)
    items = await build_source(
        cfg("rss", params={"url": "https://example.com/feed"}), http, settings
    ).fetch()
    assert len(items) == 2  # untitled entry dropped
    assert items[0].title.startswith("Introducing")
    assert items[0].published_at.isoformat() == "2026-10-31T15:00:00+00:00"
    assert items[0].tags == ["AI"]


@respx.mock
async def test_hackernews(http, settings):
    respx.get("https://hn.algolia.com/api/v1/search").respond(
        json={
            "hits": [
                {
                    "objectID": "1",
                    "title": "Cool thing",
                    "url": "https://cool.dev",
                    "author": "pg",
                    "points": 321,
                    "num_comments": 45,
                    "created_at_i": 1793534400,
                    "_tags": ["story", "front_page"],
                },
                {
                    "objectID": "2",
                    "title": "Ask HN: what are you building?",
                    "url": None,
                    "points": 5,
                    "num_comments": 2,
                    "created_at_i": 1793534400,
                    "_tags": ["ask_hn"],
                },
                {"objectID": "3", "title": None},
            ]
        }
    )
    items = await build_source(cfg("hackernews"), http, settings).fetch()
    assert len(items) == 2
    assert items[0].engagement.points == 321
    assert items[1].url == "https://news.ycombinator.com/item?id=2"
    assert items[1].tags == ["ask_hn"]


@respx.mock
async def test_github(http, settings):
    route = respx.get("https://api.github.com/search/repositories").respond(
        json={
            "items": [
                {
                    "id": 9,
                    "full_name": "acme/agentkit",
                    "html_url": "https://github.com/acme/agentkit",
                    "description": "Agents toolkit",
                    "owner": {"login": "acme"},
                    "stargazers_count": 1200,
                    "created_at": "2026-10-29T10:00:00Z",
                    "topics": ["llm", "agents"],
                    "language": "Python",
                    "forks_count": 30,
                },
                {
                    "id": 10,
                    "full_name": "x/fork",
                    "html_url": "https://github.com/x/fork",
                    "fork": True,
                },
            ]
        }
    )
    src = build_source(cfg("github", params={"topic": "llm", "min_stars": 30}), http, settings)
    items = await src.fetch()
    assert [i.title for i in items] == ["acme/agentkit"]
    assert items[0].engagement.stars == 1200 and "Python" in items[0].tags
    q = route.calls[0].request.url.params["q"]
    assert "topic:llm" in q and "stars:>=30" in q


@respx.mock
async def test_reddit_json(http, settings):
    respx.get("https://www.reddit.com/r/ML/top.json").respond(
        json={
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "a",
                            "title": "Paper link",
                            "url": "https://arxiv.org/abs/1",
                            "is_self": False,
                            "permalink": "/r/ML/comments/a/x/",
                            "score": 99,
                            "num_comments": 10,
                            "created_utc": 1793534400,
                            "link_flair_text": "Research",
                        }
                    },
                    {
                        "data": {
                            "id": "b",
                            "title": "Self post",
                            "is_self": True,
                            "selftext": "hello",
                            "permalink": "/r/ML/comments/b/y/",
                            "score": 1,
                            "created_utc": 1793534400,
                        }
                    },
                    {"data": {"id": "c", "title": "Mod post", "stickied": True}},
                ]
            }
        }
    )
    items = await build_source(cfg("reddit", params={"subreddit": "ML"}), http, settings).fetch()
    assert [i.url for i in items] == [
        "https://arxiv.org/abs/1",
        "https://www.reddit.com/r/ML/comments/b/y/",
    ]
    assert items[0].discussion_url == "https://www.reddit.com/r/ML/comments/a/x/"
    assert items[0].tags == ["Research"]


@respx.mock
async def test_reddit_falls_back_to_rss(http, settings):
    respx.get("https://www.reddit.com/r/ML/top.json").respond(status_code=403)
    respx.get("https://www.reddit.com/r/ML/top/.rss").respond(content=FEED)
    items = await build_source(cfg("reddit", params={"subreddit": "ML"}), http, settings).fetch()
    assert len(items) == 2 and items[0].discussion_url == items[0].url


@respx.mock
async def test_hf_papers(http, settings):
    respx.get("https://huggingface.co/api/daily_papers").respond(
        json=[
            {
                "paper": {
                    "id": "2410.00001",
                    "title": "Great Paper",
                    "summary": "We do X.",
                    "upvotes": 42,
                    "authors": [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}],
                },
                "publishedAt": "2026-10-31T00:00:00Z",
                "numComments": 3,
            },
        ]
    )
    [item] = await build_source(cfg("hf_papers"), http, settings).fetch()
    assert item.url == "https://arxiv.org/abs/2410.00001"
    assert item.author == "A, B, C et al."
    assert item.engagement.points == 42


@respx.mock
async def test_devto(http, settings):
    respx.get("https://dev.to/api/articles").respond(
        json=[
            {
                "id": 1,
                "title": "Build an agent",
                "url": "https://dev.to/u/agent",
                "description": "d",
                "user": {"username": "u"},
                "public_reactions_count": 50,
                "comments_count": 4,
                "published_at": "2026-10-31T08:00:00Z",
                "tag_list": ["ai", "python"],
            },
        ]
    )
    [item] = await build_source(cfg("devto", params={"tag": "ai"}), http, settings).fetch()
    assert item.engagement.points == 50 and item.tags == ["ai", "python"]


@respx.mock
async def test_retries_on_503(http, settings):
    route = respx.get("https://example.com/feed")
    route.side_effect = [httpx.Response(503), httpx.Response(200, content=FEED)]
    items = await build_source(
        cfg("rss", params={"url": "https://example.com/feed"}), http, settings
    ).fetch()
    assert len(items) == 2 and route.call_count == 2


async def test_missing_param_raises(http, settings):
    with pytest.raises(ValueError, match="params.url"):
        await build_source(cfg("rss"), http, settings).fetch()
