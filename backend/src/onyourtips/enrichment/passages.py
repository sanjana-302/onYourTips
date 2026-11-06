"""Normalize extracted text and split it into numbered passages (pure, no I/O).

Passages are what summaries cite (ADR 0002). Rules, applied to paragraphs separated by a blank
line:
  - a paragraph shorter than `min_chars` (e.g. a heading) is merged into the next one,
  - a paragraph longer than `max_chars` is split at sentence boundaries,
  - each passage records char offsets into the normalized text, so text[start:end] == passage.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from onyourtips.core.models import Passage

_SPACES = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n\s*\n+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def normalize_text(text: str) -> str:
    """One space between words, one blank line between paragraphs, no stray whitespace."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = []
    for block in _BLANKS.split(text):
        lines = [_SPACES.sub(" ", ln).strip() for ln in block.split("\n")]
        para = " ".join(ln for ln in lines if ln)
        if para:
            paragraphs.append(para)
    return "\n\n".join(paragraphs)


def content_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def word_count(text: str) -> int:
    return len(text.split())


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for para in text.split("\n\n"):
        spans.append((pos, pos + len(para)))
        pos += len(para) + 2
    return [s for s in spans if s[1] > s[0]]


def _sentence_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Sentence spans within text[start:end], without the spaces between them."""
    spans, pos = [], start
    for m in _SENTENCE_END.finditer(text, start, end):
        spans.append((pos, m.start()))
        pos = m.end()
    spans.append((pos, end))
    return [s for s in spans if s[1] > s[0]]


def _hard_cut(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Cut a span with no sentence breaks at the last space before max_chars."""
    pieces = []
    while end - start > max_chars:
        cut = text.rfind(" ", start, start + max_chars)
        if cut <= start:
            cut = start + max_chars
        pieces.append((start, cut))
        start = cut + 1 if text[cut] == " " else cut
    if end > start:
        pieces.append((start, end))
    return pieces


def _split_long(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Greedily pack whole sentences into pieces of at most max_chars."""
    if end - start <= max_chars:
        return [(start, end)]
    pieces: list[tuple[int, int]] = []
    cur_start = cur_end = None
    for s, e in _sentence_spans(text, start, end):
        if cur_start is not None and e - cur_start <= max_chars:
            cur_end = e  # sentence fits in the current piece
            continue
        if cur_start is not None:
            pieces.append((cur_start, cur_end))
        if e - s > max_chars:  # one very long sentence
            *full, last = _hard_cut(text, s, e, max_chars)
            pieces.extend(full)
            cur_start, cur_end = last
        else:
            cur_start, cur_end = s, e
    if cur_start is not None:
        pieces.append((cur_start, cur_end))
    return pieces


def split_passages(normalized: str, min_chars: int = 60, max_chars: int = 1200) -> list[Passage]:
    spans: list[tuple[int, int]] = []
    pending_start: int | None = None
    for start, end in _paragraph_spans(normalized):
        if pending_start is not None:
            start = pending_start  # absorb the short paragraph(s) before this one
            pending_start = None
        if end - start < min_chars:
            pending_start = start
            continue
        spans.extend(_split_long(normalized, start, end, max_chars))
    if pending_start is not None:  # trailing short paragraph
        if spans:
            spans[-1] = (spans[-1][0], len(normalized))
        else:
            spans.append((pending_start, len(normalized)))
    return [
        Passage(idx=i, text=normalized[s:e], char_start=s, char_end=e)
        for i, (s, e) in enumerate(spans, start=1)
    ]
