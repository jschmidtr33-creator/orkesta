"""Servir la app web."""

from fastapi.testclient import TestClient


def test_missing_app_asset_is_404_not_the_app(client: TestClient) -> None:
    """Un archivo de una versión anterior responde 404: la pestaña vieja se recarga sola en vez de
    recibir la página de la app donde esperaba código (pantalla en blanco)."""
    r = client.get("/app/assets/index-viejo123.js")
    assert r.status_code == 404
    assert r.headers.get("cache-control") == "no-store"
