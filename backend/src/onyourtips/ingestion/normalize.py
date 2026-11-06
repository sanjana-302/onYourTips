"""Pure, deterministic normalization helpers. No I/O."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
import warnings
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

from onyourtips.core.models import ContentType

# ---------------------------------------------------------------------------- URLs

_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "yclid",
    "ref",
    "ref_src",
    "ref_url",
    "referrer",
    "cmpid",
    "_hsenc",
    "_hsmi",
    "mkt_tok",
    "si",
    "spm",
    "share",
    "smid",
    "sr_share",
    "twclid",
    "s_cid",
}
_TRACKING_PREFIXES = ("utm_", "pk_", "hsa_", "oly_")
_ARXIV_PATH = re.compile(r"^/(?:abs|pdf)/(.+?)(?:v\d+)?(?:\.pdf)?$")
_CASE_INSENSITIVE_PATH_HOSTS = {"github.com"}


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in _TRACKING_PARAMS or k.startswith(_TRACKING_PREFIXES)


def canonicalize_url(url: str) -> str:
    """Normalize a URL so the same resource from different sources compares equal.

    - forces https, lowercases host, drops ``www.``, default ports and fragments
    - removes tracking query params and sorts the rest
    - removes trailing slashes and duplicate slashes
    - arXiv: ``/pdf/2401.1v2.pdf`` and ``/abs/2401.1v1`` both become ``/abs/2401.1``
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme in ("http", "https", ""):
        scheme = "https"
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    if host == "arxiv.org" and (m := _ARXIV_PATH.match(path)):
        path = f"/abs/{m.group(1)}"
    if host in _CASE_INSENSITIVE_PATH_HOSTS:
        path = path.lower()

    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _is_tracking(k)
    )
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def article_id(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]


def infer_content_type(canonical_url: str, default: ContentType) -> ContentType:
    """URL-based overrides: some hosts unambiguously determine the type."""
    host = urlsplit(canonical_url).hostname or ""
    path = urlsplit(canonical_url).path
    if host == "arxiv.org" and path.startswith("/abs/"):
        return ContentType.RESEARCH_PAPER
    if host == "huggingface.co" and path.startswith("/papers/"):
        return ContentType.RESEARCH_PAPER
    if host == "github.com" and path.count("/") == 2:  # /owner/repo
        return ContentType.PROJECT
    return default


# ---------------------------------------------------------------------------- Text

_WS = re.compile(r"\s+")
_TITLE_PREFIX = re.compile(r"^(show|ask|tell|launch)\s+hn\s*[:\-–]\s*", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def clean_text(text: str | None, max_len: int | None = None) -> str:
    if not text:
        return ""
    text = _WS.sub(" ", html.unescape(text)).strip()
    return truncate(text, max_len) if max_len else text


def html_to_text(content: str | None, max_len: int | None = None) -> str:
    """Strip HTML tags/entities and collapse whitespace."""
    if not content:
        return ""
    if "<" in content and ">" in content:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
            soup = BeautifulSoup(content, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            content = soup.get_text(" ", strip=True)
    return clean_text(content, max_len)


def truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    if " " in cut[max_len // 2 :]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:-") + "…"


def title_fingerprint(title: str) -> str | None:
    """Fingerprint for near-duplicate titles. Returns None for titles too short to be safe."""
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    t = _TITLE_PREFIX.sub("", t)
    t = _WS.sub(" ", _NON_ALNUM.sub(" ", t)).strip()
    if len(t.split()) < 4:
        return None
    return hashlib.sha1(t.encode()).hexdigest()[:16]
