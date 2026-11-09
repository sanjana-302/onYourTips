"""Deterministic checks on a summary: free, instant, run on every summary.

In eval terms these are *assertions* (code-based graders): they can't tell whether a claim is
true, but they catch whole classes of failure for free. Two kinds:

- **Gates** decide whether a summary is usable at all. A citation to a passage that doesn't exist
  is removed (a *repair*); a key point left with no valid citation is dropped; a summary left with
  no key points fails.
- **Metrics** are recorded but don't block: is each quote really in the passage it cites (exact
  or fuzzy), how much of the article is cited (coverage), duplicate points, length.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from onyourtips.core.models import Passage
from onyourtips.enrichment.summary_schema import KeyPoint, Summary

FUZZY_THRESHOLD = 0.8  # share of a quote's words found in the cited passages
DUPLICATE_THRESHOLD = 0.8  # word overlap (Jaccard) between two key points

_WORD = re.compile(r"[a-z0-9]+(?:['.-][a-z0-9]+)*")


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.split()).strip(" \"'.,;:")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class SummaryChecks(BaseModel):
    passages: int  # passages in the article
    key_points: int  # after repairs
    invalid_citations_removed: int = 0  # cited passage numbers that don't exist
    points_dropped: int = 0  # key points left with no valid citation
    quotes: int = 0  # key points with a quote
    quotes_exact: int = 0  # quote found word for word in a cited passage
    quotes_fuzzy: int = 0  # >= 80% of the quote's words found in the cited passages
    quote_elsewhere: int = 0  # quote found, but in a passage it doesn't cite (wrong citation)
    cited_passages: int = 0  # distinct passages cited
    coverage: float = 0.0  # cited_passages / passages
    duplicate_points: int = 0
    tldr_words: int = 0
    passed: bool = True
    failure: str | None = None

    @property
    def quote_match_rate(self) -> float:
        """Share of quotes found exactly or fuzzily in the passages they cite."""
        return (self.quotes_exact + self.quotes_fuzzy) / self.quotes if self.quotes else 0.0


def _quote_match(quote: str, texts: list[str]) -> str:
    """exact | fuzzy | none: is the quote in these passages?"""
    q = _norm(quote)
    if not q:
        return "none"
    joined = [_norm(t) for t in texts]
    if any(q in t for t in joined):
        return "exact"
    words = _words(quote)
    if not words:
        return "none"
    pool = set(_words(" ".join(texts)))
    return "fuzzy" if sum(w in pool for w in words) / len(words) >= FUZZY_THRESHOLD else "none"


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(_words(a)), set(_words(b))
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def check_summary(summary: Summary, passages: list[Passage]) -> tuple[Summary, SummaryChecks]:
    """Repair invalid citations, then measure. Returns the repaired summary and the checks."""
    by_idx = {p.idx: p.text for p in passages}
    checks = SummaryChecks(passages=len(passages), key_points=0)
    kept: list[KeyPoint] = []
    for kp in summary.key_points:
        valid = sorted({c for c in kp.citations if c in by_idx})
        checks.invalid_citations_removed += len(set(kp.citations)) - len(valid)
        if not valid:
            checks.points_dropped += 1
            continue
        kept.append(kp.model_copy(update={"citations": valid}))

    for kp in kept:
        if not kp.quote.strip():
            continue
        checks.quotes += 1
        match = _quote_match(kp.quote, [by_idx[c] for c in kp.citations])
        if match == "exact":
            checks.quotes_exact += 1
        elif match == "fuzzy":
            checks.quotes_fuzzy += 1
        elif _quote_match(kp.quote, list(by_idx.values())) != "none":
            checks.quote_elsewhere += 1

    cited = {c for kp in kept for c in kp.citations}
    checks.key_points = len(kept)
    checks.cited_passages = len(cited)
    checks.coverage = round(len(cited) / len(passages), 3) if passages else 0.0
    checks.duplicate_points = sum(
        1
        for i, a in enumerate(kept)
        for b in kept[i + 1 :]
        if _jaccard(a.text, b.text) >= DUPLICATE_THRESHOLD
    )
    checks.tldr_words = len(summary.tldr.split())
    if not kept:
        checks.passed = False
        checks.failure = (
            f"no key point cites a real passage ({checks.invalid_citations_removed} invalid "
            f"citations; the article has {len(passages)} passages)"
        )
    return summary.model_copy(update={"key_points": kept}), checks
