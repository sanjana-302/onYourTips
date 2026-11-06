"""Source connector interface and registry.

To add a new kind of source:
  1. add a value to ``models.SourceKind``
  2. subclass ``Source``, set ``kind``, implement ``fetch()`` and decorate with ``@register``
  3. import the module in ``sources/__init__.py``
  4. add an entry in ``config/sources.yaml``

Connectors only fetch and map fields to ``RawItem``. All cleaning, dedupe, tagging and scoring
happen in the pipeline so behaviour is consistent across sources.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import yaml

from onyourtips.core.http import HttpClient
from onyourtips.core.models import RawItem, SourceConfig, SourceKind
from onyourtips.core.settings import Settings


class Source(ABC):
    kind: ClassVar[SourceKind]

    def __init__(self, config: SourceConfig, http: HttpClient, settings: Settings) -> None:
        self.config = config
        self.http = http
        self.settings = settings

    @property
    def params(self) -> dict[str, Any]:
        return self.config.params

    def require_param(self, key: str) -> Any:
        if key not in self.params:
            raise ValueError(f"source '{self.config.id}' requires params.{key}")
        return self.params[key]

    def item(self, **fields: Any) -> RawItem:
        return RawItem(source_id=self.config.id, **fields)

    @abstractmethod
    async def fetch(self) -> list[RawItem]: ...


REGISTRY: dict[SourceKind, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    REGISTRY[cls.kind] = cls
    return cls


def build_source(config: SourceConfig, http: HttpClient, settings: Settings) -> Source:
    try:
        cls = REGISTRY[config.kind]
    except KeyError:
        raise ValueError(f"no connector registered for kind '{config.kind.value}'") from None
    return cls(config, http, settings)


def load_source_configs(path: Path) -> list[SourceConfig]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    defaults = data.get("defaults") or {}
    configs = [SourceConfig(**{**defaults, **entry}) for entry in data.get("sources") or []]
    ids = [c.id for c in configs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate source ids in {path}: {sorted(dupes)}")
    return configs
