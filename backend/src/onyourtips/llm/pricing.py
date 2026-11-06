"""Token prices from config/pricing.yaml (USD per 1M tokens)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from onyourtips.llm.models import Usage

log = logging.getLogger(__name__)

UNPRICED = "unpriced"


@dataclass(frozen=True)
class Price:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


class Pricing:
    def __init__(self, version: str, table: dict[str, dict[str, Price]]) -> None:
        self.version = version
        self.table = table

    @classmethod
    def from_yaml(cls, path: Path) -> Pricing:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        table = {
            provider: {model: Price(**(spec or {})) for model, spec in (models or {}).items()}
            for provider, models in (data.get("providers") or {}).items()
        }
        return cls(str(data.get("version", "unknown")), table)

    def price_for(self, provider: str, model: str) -> Price | None:
        models = self.table.get(provider, {})
        return models.get(model) or models.get("*")

    def cost(self, provider: str, model: str, usage: Usage) -> tuple[float, str]:
        """Return (cost in USD, pricing version used)."""
        price = self.price_for(provider, model)
        if price is None:
            log.warning("no price for %s/%s in pricing.yaml; recording cost 0", provider, model)
            return 0.0, UNPRICED
        cost = (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_read_tokens * price.cache_read
            + usage.cache_write_tokens * price.cache_write
        ) / 1_000_000
        return round(cost, 8), self.version
