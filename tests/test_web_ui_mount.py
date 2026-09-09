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


def test_ui_entry_html_is_never_served_from_cache() -> None:
    """The entry HTML names the content-hashed assets, so it must revalidate.

    Asset filenames under assets/ carry a content hash and are safe to cache
    indefinitely. popup.html cannot be hashed — it *is* the URL — and it is
    what points at those assets. A browser reusing a stale copy of it would
    load a page wired to a previous build. With no Cache-Control header at
    all, that is exactly what heuristic freshness permits, which is how a new
    popup.html once came to be paired with a month-old popup.js.
    """
    client = TestClient(fastapi_app)
    if not (_bundle_dir() / "popup.html").exists():
        import pytest
        pytest.skip("frontend/dist/popup.html not present")

    resp = client.get("/ui/")

    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_every_asset_the_entry_html_references_exists() -> None:
    """popup.html and the bundle it ships with must agree.

    This is the check that catches a half-built or half-copied bundle: HTML
    from one build next to assets from another. It cannot happen through Vite
    any more (the hash ties them together), but it can still happen through a
    partial copy into web_ui/ or a dist dropped in by hand.
    """
    import re

    bundle = _bundle_dir()
    html_path = bundle / "popup.html"
    if not html_path.exists():
        import pytest
        pytest.skip("frontend/dist/popup.html not present")

    html = html_path.read_text(encoding="utf-8")
    referenced = set(re.findall(r'(?:src|href)="([^"]+)"', html))
    local = [
        ref for ref in referenced
        if not ref.startswith(("http://", "https://", "data:", "#", "//"))
    ]
    assert local, "popup.html references no local assets — is it really the built file?"

    missing = [ref for ref in local if not (bundle / ref.lstrip("./")).exists()]
    assert not missing, f"popup.html references files absent from the bundle: {missing}"


def test_manifest_service_worker_keeps_its_unhashed_name() -> None:
    """background.js must NOT be content-hashed.

    Chrome reads "assets/background.js" literally out of manifest.json, so a
    hash there breaks the extension silently — the service worker simply never
    registers. The Vite config exempts this one entry; this pins the exemption
    so a later sweep of the output config cannot quietly undo it.
    """
    import json

    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "frontend" / "public" / "manifest.json"
    if not manifest_path.exists():
        import pytest
        pytest.skip("frontend/public/manifest.json not present")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    worker = manifest.get("background", {}).get("service_worker")
    assert worker == "assets/background.js", (
        "manifest points somewhere else now — keep it in step with vite.config.ts"
    )

    bundle = _bundle_dir()
    if not (bundle / "popup.html").exists():
        import pytest
        pytest.skip("bundle not built")
    assert (bundle / worker).exists(), (
        f"{worker} missing from the bundle — did the Vite output config start "
        "hashing it? Chrome needs this exact path."
    )
