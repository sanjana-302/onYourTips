"""LLM gateway: the only package allowed to call LLM providers (ADR 0003, 0004).

Usage:
    gateway = LLMGateway(settings, db)
    result = gateway.complete(LLMRequest.simple("ping", "Say hi"))
    result.text, result.usage, result.cost_usd, result.tokens_per_sec
"""

from onyourtips.llm.gateway import Budget, LLMGateway, build_provider
from onyourtips.llm.models import (
    BudgetExceeded,
    InvalidOutput,
    LLMError,
    LLMRequest,
    LLMResult,
    Message,
    ModelInfo,
    ModelNotFound,
    ProviderUnavailable,
    Usage,
)
from onyourtips.llm.pricing import Pricing
from onyourtips.llm.providers import OllamaProvider, Provider

__all__ = [
    "Budget",
    "BudgetExceeded",
    "InvalidOutput",
    "LLMError",
    "LLMGateway",
    "LLMRequest",
    "LLMResult",
    "Message",
    "ModelInfo",
    "ModelNotFound",
    "OllamaProvider",
    "Pricing",
    "Provider",
    "ProviderUnavailable",
    "Usage",
    "build_provider",
]
