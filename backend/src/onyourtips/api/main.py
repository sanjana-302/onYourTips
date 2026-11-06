"""FastAPI application: JSON API under /api, and the built web app (frontend/dist) at /.

Run with `oyt serve`, or: uvicorn onyourtips.api.main:create_app --factory
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from onyourtips import __version__
from onyourtips.api.routes import router
from onyourtips.core.settings import Settings, get_settings
from onyourtips.services import IngestRunner
from onyourtips.storage import Database

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="OnYourTips API",
        version=__version__,
        description="Daily AI & software digest: feed, weekly digest, interactions.",
    )
    app.state.settings = settings
    app.state.ingest = IngestRunner(settings)

    Database(settings.db_path).close()  # create the DB and apply migrations at startup
    app.include_router(router)

    dist = settings.frontend_dist
    if (dist / "index.html").exists():
        # html=True serves index.html at "/". The app uses hash routing, so no SPA fallback needed.
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    else:
        log.warning("frontend not built (%s missing) — serving API only", dist / "index.html")

        @app.get("/", include_in_schema=False)
        def root() -> dict[str, str]:
            return {
                "message": "OnYourTips API is running. Build the UI: cd frontend && npm run build",
                "docs": "/docs",
            }

    return app
