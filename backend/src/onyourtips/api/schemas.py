"""Request/response shapes for the HTTP API. Domain models are reused from core.models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from onyourtips.core.models import FeedItem, InteractionType


class FeedResponse(BaseModel):
    items: list[FeedItem]


class DigestResponse(BaseModel):
    days: int
    until: datetime
    items: list[FeedItem]


class InteractionIn(BaseModel):
    action: InteractionType
    value: int | None = Field(default=None, ge=1, le=5, description="Rating, only for action=rate")
    note: str | None = Field(default=None, max_length=1000)


class IngestStarted(BaseModel):
    started: bool  # False when a run was already in progress
