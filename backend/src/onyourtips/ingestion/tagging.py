"""Deterministic, rule-based topic tagging driven by ``config/taxonomy.yaml``.

Each topic has keywords. A keyword is matched case-insensitively on word boundaries; prefix it
with ``re:`` to supply a raw regular expression instead. Scoring per topic:

    keyword hit in title         -> +2
    keyword hit in source tags   -> +2
    each distinct hit in summary -> +1

A topic is assigned when its score >= ``min_score`` (default 2). So a title mention is enough,
but a summary needs two different keywords — this keeps passing mentions from tagging an item.
Semantic/embedding-based topics can be layered on later without replacing this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Topic:
    slug: str
    label: str
    patterns: tuple[re.Pattern[str], ...]


def _compile(keyword: str) -> re.Pattern[str]:
    if keyword.startswith("re:"):
        return re.compile(keyword[3:], re.IGNORECASE)
    return re.compile(rf"(?<![\w]){re.escape(keyword)}(?![\w])", re.IGNORECASE)


class Tagger:
    def __init__(self, topics: list[Topic], min_score: int = 2) -> None:
        self.topics = topics
        self.min_score = min_score

    @classmethod
    def from_dict(cls, data: dict) -> Tagger:
        topics = [
            Topic(
                slug=slug,
                label=spec.get("label", slug),
                patterns=tuple(_compile(k) for k in spec.get("keywords", [])),
            )
            for slug, spec in (data.get("topics") or {}).items()
        ]
        return cls(topics, min_score=int(data.get("min_score", 2)))

    @classmethod
    def from_yaml(cls, path: Path) -> Tagger:
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f) or {})

    def tag(
        self, title: str, summary: str | None = None, source_tags: list[str] | None = None
    ) -> list[str]:
        summary = summary or ""
        tags_text = " | ".join(t.replace("-", " ") for t in (source_tags or []))
        found: list[str] = []
        for topic in self.topics:
            score = 0
            for p in topic.patterns:
                if p.search(title):
                    score += 2
                if tags_text and p.search(tags_text):
                    score += 2
                if summary and p.search(summary):
                    score += 1
                if score >= self.min_score:
                    found.append(topic.slug)
                    break
        return found
