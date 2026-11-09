"""What a summary looks like (the output contract). Validated with pydantic before saving."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KeyPoint(BaseModel):
    text: str = Field(min_length=3)
    citations: list[int] = Field(default_factory=list)  # passage numbers (P1 = 1)
    quote: str = ""  # short verbatim phrase from a cited passage: makes the claim checkable


class Summary(BaseModel):
    tldr: str = Field(min_length=3)
    key_points: list[KeyPoint] = Field(min_length=1, max_length=8)
    why_it_matters: str = ""


class MapNotes(BaseModel):
    """Map step of map-reduce: cited notes from one chunk of a long article."""

    points: list[KeyPoint] = Field(default_factory=list, max_length=8)
