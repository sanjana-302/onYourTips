"""Deterministic ranking.

    engagement(s) = log1p(points + 1.5*comments + 0.5*stars)
    signal        = sum over sightings s of  weight(source) * (1 + engagement(s))
    score(now)    = signal * 0.5 ** (age_hours / half_life_hours)

``signal`` is stored on the article (time-independent). ``score`` is computed at query time.
The ``1 +`` means a sighting counts even without engagement data (e.g. RSS), and an article
seen on several sources naturally outranks one seen on a single source.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import datetime

from onyourtips.core.models import Engagement, Sighting


def engagement_value(e: Engagement) -> float:
    raw = (e.points or 0) + 1.5 * (e.comments or 0) + 0.5 * (e.stars or 0)
    return math.log1p(max(raw, 0.0))


def compute_signal(sightings: Iterable[Sighting], weights: Mapping[str, float]) -> float:
    return round(
        sum(
            weights.get(s.source_id, 1.0) * (1.0 + engagement_value(s.engagement))
            for s in sightings
        ),
        4,
    )


def rank_score(
    signal: float, published_at: datetime, now: datetime, half_life_hours: float
) -> float:
    age_h = max((now - published_at).total_seconds() / 3600.0, 0.0)
    return round(signal * 0.5 ** (age_h / half_life_hours), 4)
