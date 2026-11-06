from datetime import datetime, timezone

import pytest

from onyourtips.core.models import ContentType
from onyourtips.core.utils import parse_datetime, unique
from onyourtips.ingestion.normalize import (
    canonicalize_url,
    html_to_text,
    infer_content_type,
    title_fingerprint,
    truncate,
)


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "http://www.Example.com/Post/?utm_source=x&b=2&a=1#frag",
            "https://example.com/Post?a=1&b=2",
        ),
        ("https://example.com:443//a//b/", "https://example.com/a/b"),
        ("https://example.com/?fbclid=abc", "https://example.com/"),
        ("https://arxiv.org/abs/2410.12345v3", "https://arxiv.org/abs/2410.12345"),
        ("https://arxiv.org/pdf/2410.12345v1.pdf", "https://arxiv.org/abs/2410.12345"),
        ("https://arxiv.org/pdf/2410.12345", "https://arxiv.org/abs/2410.12345"),
        ("https://GitHub.com/Owner/Repo", "https://github.com/owner/repo"),
        ("https://www.youtube.com/watch?v=abc&si=track", "https://youtube.com/watch?v=abc"),
        ("https://example.com:8080/x", "https://example.com:8080/x"),
    ],
)
def test_canonicalize_url(url, expected):
    assert canonicalize_url(url) == expected


def test_canonicalize_is_idempotent():
    u = canonicalize_url("http://www.example.com/a/?utm_campaign=1&z=1")
    assert canonicalize_url(u) == u


def test_infer_content_type():
    assert (
        infer_content_type("https://arxiv.org/abs/1", ContentType.NEWS)
        == ContentType.RESEARCH_PAPER
    )
    assert infer_content_type("https://github.com/a/b", ContentType.NEWS) == ContentType.PROJECT
    assert infer_content_type("https://github.com/a/b/issues", ContentType.NEWS) == ContentType.NEWS
    assert (
        infer_content_type("https://example.com/x", ContentType.BLOG_POST) == ContentType.BLOG_POST
    )


def test_html_to_text():
    assert html_to_text("<p>Hello&nbsp;<b>world</b></p><script>x()</script>") == "Hello world"
    assert html_to_text("plain &amp; simple") == "plain & simple"
    assert html_to_text(None) == ""


def test_truncate_on_word_boundary():
    out = truncate("the quick brown fox jumps over the lazy dog", 20)
    assert out.endswith("…") and len(out) <= 20 and " " not in out[-2:]


def test_title_fingerprint():
    a = title_fingerprint("Show HN: My Tiny Rust Database Engine!")
    b = title_fingerprint("my tiny rust database engine")
    assert a == b is not None
    assert title_fingerprint("Too short") is None


def test_parse_datetime_variants():
    expected = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
    assert parse_datetime("2026-10-31T15:00:00Z") == expected
    assert parse_datetime("Sat, 31 Oct 2026 15:00:00 GMT") == expected
    assert parse_datetime("2026-10-31T20:30:00+05:30") == expected
    assert parse_datetime(int(expected.timestamp())) == expected
    assert parse_datetime(expected.timetuple()) == expected
    assert parse_datetime("not a date") is None
    assert parse_datetime(None) is None


def test_unique():
    assert unique(["AI", "ai", " ml ", "", "ML", "rust"]) == ["AI", "ml", "rust"]
