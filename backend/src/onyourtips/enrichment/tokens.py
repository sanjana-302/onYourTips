"""Counting tokens without calling a model.

Two counters, same interface:
- HFCounter: the model's real tokenizer from Hugging Face (`tokenizers` library). Exact for the
  text itself; the chat template adds a few tokens (TEMPLATE_OVERHEAD_TOKENS).
- RatioCounter: characters / chars-per-token. The fallback for models with no tokenizer
  configured. The ratio starts at a rule of thumb (~4 for English) and is replaced by one measured
  with `oyt tokens calibrate`.
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import yaml

log = logging.getLogger(__name__)

DEFAULT_CHARS_PER_TOKEN = 4.0
# Chat templates wrap each message in role markers (llama3.2 also adds a short default system
# header with dates). An allowance per call; calibration shows the real difference.
TEMPLATE_OVERHEAD_TOKENS = 30


class TokenCounter(Protocol):
    name: str  # stored with every count: hf:<repo> or ratio:<chars-per-token>

    def count(self, text: str) -> int: ...


class RatioCounter:
    def __init__(self, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> None:
        self.chars_per_token = chars_per_token
        self.name = f"ratio:{chars_per_token:.2f}"

    def count(self, text: str) -> int:
        return estimate_tokens(len(text), self.chars_per_token)


class HFCounter:
    def __init__(self, tokenizer, repo: str) -> None:
        self.tokenizer = tokenizer
        self.name = f"hf:{repo}"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)


def tokenizer_repo(tokenizers_file: Path, model: str) -> str | None:
    """Hugging Face repo for a model: exact name first, then the family before ':'."""
    try:
        with open(tokenizers_file, encoding="utf-8") as f:
            models = (yaml.safe_load(f) or {}).get("models") or {}
    except OSError:
        return None
    return models.get(model) or models.get(model.split(":", 1)[0])


@lru_cache(maxsize=8)
def load_hf_tokenizer(repo: str):
    """Download (first time) or load from cache. None if unavailable: callers fall back."""
    try:
        from tokenizers import Tokenizer
    except ImportError:
        log.warning("`tokenizers` isn't installed (uv sync); using a characters-per-token estimate")
        return None
    try:
        return Tokenizer.from_pretrained(repo)
    except Exception as exc:  # offline, repo renamed, ...
        log.warning(
            "couldn't load tokenizer %s (%s); using a characters-per-token estimate", repo, exc
        )
        return None


def estimate_tokens(chars: int, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> int:
    if chars <= 0:
        return 0
    return math.ceil(chars / chars_per_token)


def percentile(values: list[int], p: float) -> int:
    """Nearest-rank percentile (p in 0..100) of a list of ints; 0 for an empty list."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]
