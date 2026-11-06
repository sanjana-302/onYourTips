"""Weekly digest selection — deterministic and explainable.

Input: candidate FeedItems for the window, already ranked by score (signal, no recency decay)
and already filtered for quality (``signal >= min_signal``) and dismissals.

1. **Primary topic.** Each article gets one primary topic: the *rarest* of its topics within the
   candidate pool (ties broken by name). An item tagged ``llms + ai-coding`` counts as
   ``ai-coding``, so the common ``llms`` tag doesn't use up the cap for everything.
   Untagged items fall into ``other``.
2. **Type quotas first.** For each content type with a minimum (e.g. 6 projects, 4 papers), take
   the best items of that type, respecting topic caps.
3. **Fill by score.** Fill the remaining slots with the best remaining items, respecting caps.
4. **Relax if short.** If caps left slots empty, fill them by score ignoring caps.

Output is sorted by score.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from onyourtips.core.models import ContentType, FeedItem

OTHER = "other"


@dataclass
class DigestConfig:
    limit: int = 30
    max_per_topic: int = 5
    min_per_type: dict[ContentType, int] = field(default_factory=dict)


def primary_topics(items: list[FeedItem]) -> dict[str, str]:
    freq = Counter(t for i in items for t in i.article.topics)
    return {
        i.article.id: min(i.article.topics, key=lambda t: (freq[t], t))
        if i.article.topics
        else OTHER
        for i in items
    }


def select_digest(candidates: list[FeedItem], cfg: DigestConfig) -> list[FeedItem]:
    pool = sorted(candidates, key=lambda i: (i.score, i.article.id), reverse=True)
    primary = primary_topics(pool)
    chosen: list[FeedItem] = []
    chosen_ids: set[str] = set()
    topic_count: Counter[str] = Counter()

    def take(item: FeedItem, respect_cap: bool = True) -> bool:
        aid = item.article.id
        if aid in chosen_ids or len(chosen) >= cfg.limit:
            return False
        if respect_cap and topic_count[primary[aid]] >= cfg.max_per_topic:
            return False
        chosen.append(item)
        chosen_ids.add(aid)
        topic_count[primary[aid]] += 1
        return True

    # 2. quotas per content type
    for ctype, minimum in cfg.min_per_type.items():
        got = 0
        for item in pool:
            if got >= minimum:
                break
            if item.article.content_type == ctype and take(item):
                got += 1

    # 3. fill by score with caps
    for item in pool:
        take(item)

    # 4. relax caps if we still have room
    for item in pool:
        take(item, respect_cap=False)

    return sorted(chosen, key=lambda i: (i.score, i.article.id), reverse=True)
