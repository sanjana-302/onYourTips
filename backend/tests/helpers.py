"""Shared constants and builders for tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from onyourtips.core.models import Article, RawItem, Sighting, SourceConfig
from onyourtips.ingestion.pipeline import normalize
from onyourtips.ingestion.tagging import Tagger

BACKEND = Path(__file__).resolve().parents[1]
CONFIG = BACKEND / "config"
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)

HN = SourceConfig(id="hn", name="HN", kind="hackernews", content_type="news")
GH = SourceConfig(id="gh", name="GH", kind="github", content_type="project")
RSS = SourceConfig(id="blog", name="Blog", kind="rss", content_type="blog_post", weight=2)
W = {"hn": 1.0, "gh": 1.0, "blog": 2.0}


def normalize_raw(
    tagger: Tagger, cfg: SourceConfig, **raw_fields
) -> tuple[Article, Sighting] | None:
    """Build a RawItem for ``cfg`` and run it through the pipeline's normalize step at NOW."""
    return normalize(RawItem(source_id=cfg.id, **raw_fields), cfg, tagger, NOW)
