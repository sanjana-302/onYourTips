"""Full-text extraction for selected articles (ADR 0002). No LLM involved.

Strategy per article, picked from its URL:
  - GitHub repo   -> the README, via the GitHub API (the repo page itself is mostly UI)
  - arXiv paper   -> the abstract we already have from the feed (PDF parsing comes later)
  - PDF / video / social posts -> skipped; fall back to the feed description
  - anything else -> fetch the page (respecting robots.txt) and extract the main text with
                     trafilatura, or a BeautifulSoup fallback when trafilatura isn't installed

If the result is too short to summarize, the feed's own description is used instead and the
status is "excerpt", so later features know the summary is based on a blurb.
"""

from __future__ import annotations

import asyncio
import logging
import re
import warnings
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

from onyourtips.core.models import Article, ArticleContent, ContentStatus, Passage
from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow
from onyourtips.enrichment.passages import content_hash, normalize_text, split_passages, word_count

log = logging.getLogger(__name__)

EXTRACTOR_VERSION = "1"
SKIP_EXTENSIONS = (".pdf", ".zip", ".mp4", ".mp3", ".png", ".jpg", ".jpeg", ".gif")
SKIP_HOSTS = {"youtube.com", "youtu.be", "x.com", "twitter.com", "vimeo.com", "tiktok.com"}

try:  # optional at import time so the rest of the app works without it
    import trafilatura  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - depends on the environment
    trafilatura = None


class Extraction:
    """Result of extracting one article."""

    def __init__(self, content: ArticleContent, passages: list[Passage]) -> None:
        self.content = content
        self.passages = passages


class RobotsCache:
    """robots.txt per host, fetched once per run. Unreachable robots.txt means allowed."""

    def __init__(self, http: httpx.AsyncClient, user_agent: str) -> None:
        self.http = http
        self.user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._lock = asyncio.Lock()

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self._lock:
            if origin not in self._parsers:
                self._parsers[origin] = await self._fetch(origin)
        parser = self._parsers[origin]
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    async def _fetch(self, origin: str) -> RobotFileParser | None:
        try:
            resp = await self.http.get(f"{origin}/robots.txt", timeout=8.0)
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        return parser


class Extractor:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http
        self.robots = RobotsCache(http, settings.user_agent)
        self._host_locks: dict[str, asyncio.Lock] = {}

    async def extract(self, article: Article) -> Extraction:
        url = article.url
        parts = urlsplit(article.canonical_url)
        host = (parts.hostname or "").removeprefix("www.")
        path = parts.path

        try:
            if host == "github.com" and path.count("/") == 2:
                return await self._github_readme(article, path.strip("/"))
            if host == "arxiv.org" and path.startswith("/abs/"):
                return self._from_feed(
                    article,
                    extractor="feed",
                    status=ContentStatus.OK,
                    note="arXiv abstract from the feed",
                )
            if host in SKIP_HOSTS or path.lower().endswith(SKIP_EXTENSIONS):
                return self._from_feed(
                    article, reason=f"not an article page ({host or 'unknown'})", skipped=True
                )
            if not await self.robots.allowed(url):
                return self._from_feed(
                    article, reason="blocked by the site's robots.txt", skipped=True
                )
            return await self._fetch_page(article)
        except httpx.HTTPError as exc:  # timeouts, connection errors: worth retrying tomorrow
            return self._from_feed(
                article, reason=f"fetch failed: {type(exc).__name__}: {exc}", retryable=True
            )

    # ------------------------------------------------------------------ strategies

    async def _fetch_page(self, article: Article) -> Extraction:
        host = urlsplit(article.url).hostname or ""
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:  # politeness: one request at a time per site
            resp = await self.http.get(article.url, headers={"Accept": "text/html,*/*;q=0.5"})
        if resp.status_code >= 400:
            return self._from_feed(
                article, reason=f"HTTP {resp.status_code}", retryable=_transient(resp.status_code)
            )
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype:
            return self._from_feed(
                article, reason=f"not HTML ({ctype or 'unknown type'})", skipped=True
            )
        if len(resp.content) > self.settings.extract_max_bytes:
            return self._from_feed(article, reason="page too large")

        html = resp.text
        if trafilatura is not None:
            text = (
                trafilatura.extract(
                    html,
                    url=article.url,
                    include_comments=False,
                    include_tables=False,
                    favor_precision=True,
                )
                or ""
            )
            extractor = "trafilatura"
        else:
            text = fallback_extract(html)
            extractor = "html-fallback"
        return self._finish(article, text, extractor, source_url=str(resp.url))

    async def _github_readme(self, article: Article, repo: str) -> Extraction:
        headers = {
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        api_url = f"https://api.github.com/repos/{repo}/readme"
        resp = await self.http.get(api_url, headers=headers)
        if resp.status_code >= 400:
            return self._from_feed(
                article,
                reason=f"no README (HTTP {resp.status_code})",
                retryable=_transient(resp.status_code),
            )
        text = markdown_to_text(resp.text)
        if article.summary and article.summary not in text:
            text = f"{article.summary}\n\n{text}"  # the repo description is useful context
        return self._finish(article, text, "github-readme", source_url=api_url)

    # ------------------------------------------------------------------ helpers

    def _finish(self, article: Article, raw: str, extractor: str, source_url: str) -> Extraction:
        text = normalize_text(raw)
        if word_count(text) < self.settings.extract_min_words:
            return self._from_feed(
                article, reason=f"page text too short ({word_count(text)} words via {extractor})"
            )
        return self._build(article, text, extractor, source_url, ContentStatus.OK)

    def _from_feed(
        self,
        article: Article,
        *,
        reason: str | None = None,
        note: str | None = None,
        extractor: str = "feed",
        status: ContentStatus = ContentStatus.EXCERPT,
        skipped: bool = False,
        retryable: bool = False,
    ) -> Extraction:
        text = normalize_text(article.summary or "")
        if not text:
            final = ContentStatus.SKIPPED if skipped else ContentStatus.FAILED
            return self._build(
                article, "", extractor, article.url, final, error=reason, retryable=retryable
            )
        return self._build(
            article,
            text,
            extractor,
            article.url,
            status,
            error=reason or note,
            retryable=retryable,
        )

    def _build(
        self, article, text, extractor, source_url, status, error=None, retryable=False
    ) -> Extraction:
        content = ArticleContent(
            article_id=article.id,
            content_hash=content_hash(text) if text else None,
            extractor=extractor,
            extractor_version=EXTRACTOR_VERSION,
            source_url=source_url,
            text=text or None,
            word_count=word_count(text),
            status=status,
            error=error,
            retryable=retryable,
            fetched_at=utcnow(),
        )
        return Extraction(content, split_passages(text) if text else [])


# ----------------------------------------------------------------------------- pure helpers


def _transient(status_code: int) -> bool:
    """Server errors and rate limiting may succeed tomorrow; 4xx like 403/404 won't."""
    return status_code >= 500 or status_code == 429


_BOILERPLATE_TAGS = [
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "svg",
    "button",
    "iframe",
]
_BLOCK_TAGS = ["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote"]


def fallback_extract(html: str) -> str:
    """Simple main-text extraction: prefer <article>, then <main>, then <body>."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_BOILERPLATE_TAGS):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = []
    for el in root.find_all(_BLOCK_TAGS):
        if el.find_parent(_BLOCK_TAGS):  # skip <p> inside <li> etc.; the parent has the text
            continue
        text = el.get_text(" ", strip=True)
        if text:
            blocks.append(text)
    return "\n\n".join(blocks)


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_HTML = re.compile(r"<[^>]+>")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_MD_EMPHASIS = re.compile(r"(\*\*|__|`)")
_MD_FENCE = re.compile(r"^```.*?^```\s*$", re.MULTILINE | re.DOTALL)
_MD_LIST = re.compile(r"^\s*([-*+]|\d+\.)\s+", re.MULTILINE)


def markdown_to_text(md: str) -> str:
    """README markdown to plain paragraphs: drop images/badges, code blocks and HTML; keep text."""
    text = _MD_FENCE.sub("", md)
    text = _MD_IMAGE.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HTML.sub("", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_EMPHASIS.sub("", text)
    # each list item becomes its own paragraph so passages stay meaningful
    text = _MD_LIST.sub("\n", text)
    return text
