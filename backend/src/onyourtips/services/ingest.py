"""Run the refresh pipeline in the background so the API (and the phone UI) stays responsive."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from pydantic import BaseModel

from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow

log = logging.getLogger(__name__)


class IngestStatus(BaseModel):
    running: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    totals: dict[str, int] | None = None
    failed_sources: list[str] = []
    extracted: int | None = None  # articles with new full text
    error: str | None = None


class IngestRunner:
    """At most one ingestion at a time, on its own thread with its own DB connection."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status = IngestStatus(running=False)

    @property
    def status(self) -> IngestStatus:
        return self._status.model_copy()

    def start(self) -> bool:
        """Start a run. Returns False if one is already in progress."""
        with self._lock:
            if self._status.running:
                return False
            self._status = IngestStatus(running=True, started_at=utcnow())
            self._thread = threading.Thread(target=self._run, name="oyt-ingest", daemon=True)
            self._thread.start()
            return True

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        report = None
        error: str | None = None
        try:
            report = self._ingest()
            error = report.error
        except Exception as exc:  # surfaced in status, never crashes the API
            log.exception("background refresh failed")
            error = f"{type(exc).__name__}: {exc}"
        ingest = report.ingest if report else {}
        with self._lock:
            self._status = IngestStatus(
                running=False,
                started_at=self._status.started_at,
                finished_at=utcnow(),
                totals={k: v for k, v in ingest.items() if isinstance(v, int)} or None,
                failed_sources=ingest.get("failed_sources", []),
                extracted=report.extract.get("ok") if report else None,
                error=error,
            )

    def _ingest(self):
        """The Refresh button runs the same pipeline as the 1 AM schedule, recorded as manual."""
        # Imported lazily: connectors pull in feedparser etc., not needed for read-only use.
        from onyourtips.services.nightly import run_nightly
        from onyourtips.storage import Database

        with Database(self.settings.db_path) as db:
            # The app's Refresh button shouldn't spin for an hour: summaries happen at night,
            # or on demand when a story is opened.
            return run_nightly(self.settings, db, trigger="manual", summarize=False)
