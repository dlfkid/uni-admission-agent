"""Tests for the `/ui/` static mount serving the frontend popup as
a standalone web app.

The same Vite-built bundle that powers the Chrome extension popup is
served over HTTP from FastAPI. Users can open `http://localhost:8910/ui/`
in any browser — no extension install required.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.server import app as fastapi_app


def _bundle_dir() -> Path:
    """Locate the built frontend bundle, preferring the new path."""
    repo_root = Path(__file__).resolve().parent.parent
    new_path = repo_root / "frontend" / "dist"
    if new_path.exists():
        return new_path
    return repo_root / "extension" / "dist"


def test_ui_root_returns_html() -> None:
    """GET /ui/ returns popup.html (200 + content-type:html)."""
    client = TestClient(fastapi_app)
    resp = client.get("/ui/")

    # The web UI bundle MUST be built before this test passes — if missing,
    # the test skips rather than misleadingly passing as 404.
    bundle = _bundle_dir()
    if not (bundle / "popup.html").exists():
        import pytest
        pytest.skip(
            "frontend/dist/popup.html not present — "
            "run `npm run build --prefix frontend`"
        )

    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "popup" in resp.text.lower()


def test_ui_serves_static_assets() -> None:
    """Asset paths under /ui/assets/* should be served too."""
    client = TestClient(fastapi_app)
    assets_dir = _bundle_dir() / "assets"
    if not assets_dir.exists() or not list(assets_dir.glob("*.js")):
        import pytest
        pytest.skip(
            "frontend/dist/assets not built — "
            "run `npm run build --prefix frontend`"
        )

    sample_js = next(assets_dir.glob("*.js"))
    resp = client.get(f"/ui/assets/{sample_js.name}")
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("content-type", "")


def test_ui_index_redirect_or_directly_serves() -> None:
    """Visiting /ui (no trailing slash) should still resolve — either via
    a 30x redirect to /ui/ or by serving popup.html directly."""
    client = TestClient(fastapi_app)
    if not (_bundle_dir() / "popup.html").exists():
        import pytest
        pytest.skip("frontend/dist/popup.html not present")

    resp = client.get("/ui", follow_redirects=False)
    assert resp.status_code in (200, 301, 307, 308)
