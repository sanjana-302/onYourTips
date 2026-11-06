"""Importing this package registers all connectors."""

from onyourtips.ingestion.sources import (  # noqa: F401
    arxiv,
    devto,
    github,
    hackernews,
    hf_papers,
    reddit,
    rss,
)
from onyourtips.ingestion.sources.base import (
    REGISTRY,
    Source,
    build_source,
    load_source_configs,
    register,
)

__all__ = ["REGISTRY", "Source", "build_source", "load_source_configs", "register"]
