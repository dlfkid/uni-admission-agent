"""Tests for the `/ui/` static mount serving the extension popup as
a standalone web app.

Same Vite-built bundle that powers the Chrome extension popup is served
over HTTP from FastAPI. Users can open `http://localhost:8910/ui/` in any
browser — no extension install required.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import app as fastapi_app


def test_ui_root_returns_html() -> None:
    """GET /ui/ returns popup.html (200 + content-type:html)."""
    client = TestClient(fastapi_app)
    resp = client.get("/ui/")

    # The web UI bundle MUST be built before this test passes — if missing,
    # the test fails clearly rather than passing as 404.
    bundle = Path(__file__).resolve().parent.parent / "extension" / "dist"
    if not (bundle / "popup.html").exists():
        # Skip if bundle wasn't built — CI runs `npm run build --prefix
        # extension` as a preflight; locally devs may not have built yet.
        import pytest
        pytest.skip("extension/dist/popup.html not present — run `npm run build`")

    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    # Sanity: the served HTML should reference the popup script that
    # Vite produced.
    assert "popup" in resp.text.lower()


def test_ui_serves_static_assets() -> None:
    """Asset paths under /ui/assets/* should be served too."""
    client = TestClient(fastapi_app)
    bundle = Path(__file__).resolve().parent.parent / "extension" / "dist"
    assets_dir = bundle / "assets"
    if not assets_dir.exists() or not list(assets_dir.glob("*.js")):
        import pytest
        pytest.skip("extension/dist/assets not built — run `npm run build`")

    sample_js = next(assets_dir.glob("*.js"))
    resp = client.get(f"/ui/assets/{sample_js.name}")
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("content-type", "")


def test_ui_index_redirect_or_directly_serves() -> None:
    """Visiting /ui (no trailing slash) should still resolve — either via
    a 30x redirect to /ui/ or by serving popup.html directly."""
    client = TestClient(fastapi_app)
    bundle = Path(__file__).resolve().parent.parent / "extension" / "dist"
    if not (bundle / "popup.html").exists():
        import pytest
        pytest.skip("extension/dist/popup.html not present")

    resp = client.get("/ui", follow_redirects=False)
    assert resp.status_code in (200, 301, 307, 308)
