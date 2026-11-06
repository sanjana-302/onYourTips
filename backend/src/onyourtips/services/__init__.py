"""Use cases shared by the CLI and the API.

Routers and commands call these, never storage directly.
"""

from onyourtips.services.articles import (
    AppStatus,
    SourceInfo,
    TopicInfo,
    TopicInterest,
    app_status,
    get_article,
    get_feed,
    interests,
    list_saved,
    list_sources,
    list_topics,
    record_interaction,
)
from onyourtips.services.content import (
    ContentView,
    ExtractResult,
    extract_articles,
    get_content,
    select_for_extraction,
)
from onyourtips.services.digest import build_digest
from onyourtips.services.ingest import IngestRunner, IngestStatus

__all__ = [
    "AppStatus",
    "ContentView",
    "ExtractResult",
    "IngestRunner",
    "IngestStatus",
    "SourceInfo",
    "TopicInfo",
    "TopicInterest",
    "app_status",
    "build_digest",
    "extract_articles",
    "get_content",
    "get_article",
    "get_feed",
    "interests",
    "list_saved",
    "list_sources",
    "list_topics",
    "record_interaction",
    "select_for_extraction",
]
