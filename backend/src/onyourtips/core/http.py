"""Thin async HTTP wrapper with retries on transient failures."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from onyourtips.core.settings import Settings

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(self, client: httpx.AsyncClient, retries: int = 2, backoff: float = 1.0) -> None:
        self._client = client
        self.retries = retries
        self.backoff = backoff

    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        for attempt in range(self.retries + 1):
            last = attempt == self.retries
            try:
                resp = await self._client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                if last:
                    raise
                log.debug("transport error %s on %s, retrying", exc, url)
                await asyncio.sleep(self.backoff * 2**attempt)
                continue
            if resp.status_code in RETRY_STATUS and not last:
                wait = self.backoff * 2**attempt
                retry_after = resp.headers.get("retry-after", "")
                if retry_after.isdigit():
                    wait = min(float(retry_after), 30.0)
                log.debug("HTTP %s on %s, retrying in %.1fs", resp.status_code, url, wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")  # pragma: no cover

    async def get_json(self, url: str, **kw: Any) -> Any:
        return (await self.get(url, **kw)).json()


@asynccontextmanager
async def build_http_client(settings: Settings) -> AsyncIterator[HttpClient]:
    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    ) as client:
        yield HttpClient(client, retries=settings.http_retries, backoff=settings.http_backoff)
