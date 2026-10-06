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
    from src.config import get_settings
    settings = get_settings()
    ui_path = Path(__file__).parent / "dashboard.html"
    raw = ui_path.read_text(encoding="utf-8")
    sb_url = settings.supabase.url or ""
    sb_key = settings.supabase.anon_key or ""
    rendered = raw.replace("{{SUPABASE_URL}}", sb_url).replace("{{SUPABASE_ANON_KEY}}", sb_key)
    return rendered


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    """Serve the MATECOS control-plane dashboard."""
    return HTMLResponse(content=_load_html())


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_alias() -> HTMLResponse:
    """Alias for the dashboard."""
    return HTMLResponse(content=_load_html())
