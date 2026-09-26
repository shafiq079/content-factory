"""Development CORS policy should accept temporary preview origins such as Codespaces."""
from pathlib import Path

from fastapi.testclient import TestClient

from app import core
from app.main import app, cors_origins


def test_cors_allows_arbitrary_preview_origin(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    with TestClient(app) as client:
        response = client.options(
            "/projects",
            headers={
                "Origin": "https://example-codespace-3000.app.github.dev",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()


def test_cors_origins_are_environment_driven(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://one.example, https://two.example")
    assert cors_origins() == ["https://one.example", "https://two.example"]

    monkeypatch.delenv("CORS_ALLOW_ORIGINS")
    monkeypatch.setenv("FRONTEND_URL", "https://frontend.example")
    assert cors_origins() == ["https://frontend.example"]
