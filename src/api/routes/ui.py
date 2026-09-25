"""Web UI route — serves the MATECOS dashboard as a single-page app."""

from __future__ import annotations

import importlib.resources as _res
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Dashboard"])

_UI_HTML: str | None = None


def _load_html() -> str:
    global _UI_HTML
    if _UI_HTML is None:
        ui_path = Path(__file__).parent / "dashboard.html"
        _UI_HTML = ui_path.read_text(encoding="utf-8")
    return _UI_HTML


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    """Serve the MATECOS control-plane dashboard."""
    return HTMLResponse(content=_load_html())


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_alias() -> HTMLResponse:
    """Alias for the dashboard."""
    return HTMLResponse(content=_load_html())
