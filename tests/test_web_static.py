"""Web API — serving the built frontend.

In production the API and the SPA share one origin, so the app has to answer
both. The interesting cases are the ones Starlette's ``StaticFiles`` gets wrong
on its own: client-side routes must return the shell with a 200, while unknown
API paths must stay 404s rather than being masked by it.
"""
import httpx
import pytest
from httpx import ASGITransport

from web_api.main import create_app
from tests.web_helpers import build_test_settings

INDEX_HTML = "<!doctype html><title>School</title><div id=root></div>"


@pytest.fixture
def dist(tmp_path):
    """A minimal stand-in for the Vite build output."""
    (tmp_path / "index.html").write_text(INDEX_HTML)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-abc123.js").write_text("console.log('app')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    return tmp_path


def _client(dist_dir):
    settings = build_test_settings(web_dist_dir=str(dist_dir))
    app = create_app(settings=settings)
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


async def test_root_serves_the_shell(dist):
    async with _client(dist) as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert resp.text == INDEX_HTML


async def test_client_side_route_serves_the_shell(dist):
    """A hard refresh on a React Router path must not 404."""
    async with _client(dist) as client:
        resp = await client.get("/classes/-100123/homework")
        assert resp.status_code == 200
        assert resp.text == INDEX_HTML


async def test_hashed_asset_is_served(dist):
    async with _client(dist) as client:
        resp = await client.get("/assets/app-abc123.js")
        assert resp.status_code == 200
        assert "console.log" in resp.text


async def test_root_level_file_is_served_as_itself(dist):
    """favicon and friends must not be replaced by the shell."""
    async with _client(dist) as client:
        resp = await client.get("/favicon.svg")
        assert resp.status_code == 200
        assert resp.text == "<svg/>"


async def test_unknown_api_path_still_404s(dist):
    """The catch-all must not turn an API 404 into a 200 page of HTML."""
    async with _client(dist) as client:
        resp = await client.get("/api/v1/does-not-exist")
        assert resp.status_code == 404
        assert resp.text != INDEX_HTML


async def test_api_routes_still_work(dist):
    """Registration order: the catch-all must not shadow real endpoints."""
    async with _client(dist) as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200


async def test_unauthenticated_api_call_still_401s(dist):
    """A 401 must survive too — otherwise the SPA can never detect a lost session."""
    async with _client(dist) as client:
        resp = await client.get("/api/v1/me")
        assert resp.status_code == 401


@pytest.mark.parametrize("path", ["/..%2foutside.txt", "/%2e%2e/outside.txt"])
async def test_path_traversal_is_refused(dist, tmp_path, path):
    """A crafted path must not reach files outside the build directory.

    The traversal has to be percent-encoded to be worth testing: a plain
    ``/../outside.txt`` is collapsed to ``/outside.txt`` by the client before it
    is ever sent, so it would pass without exercising anything. ``%2f`` survives
    and reaches the handler as ``../outside.txt``.

    The target file deliberately exists, so serving the shell instead is proof
    the containment check fired rather than the file merely being absent.
    """
    (tmp_path.parent / "outside.txt").write_text("do not serve me")

    async with _client(dist) as client:
        resp = await client.get(path)
        assert "do not serve me" not in resp.text
        assert resp.text == INDEX_HTML


async def test_app_starts_without_a_build(tmp_path):
    """Development and CI run the API with no dist directory at all."""
    missing = tmp_path / "nope"
    async with _client(missing) as client:
        assert (await client.get("/api/v1/health")).status_code == 200
        # No shell to serve, so the SPA route is simply not registered.
        assert (await client.get("/classes/-1/today")).status_code == 404
